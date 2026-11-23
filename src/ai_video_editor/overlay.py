"""Phase 2.5.1.7 — static text overlay cards (transparent PNG layers).

The renderer composites `scene.overlay` AFTER zoom/pan, so text stays sharp and
still while the illustration moves. Cards are generated locally from the
"TEXT OVERLAY:" section of each Script scene prompt — no API call, no cost.

Prompt convention (written by the script prompt template):

    <illustration description> TEXT OVERLAY: 上司（じょうし）; Romaji: jōshi;
    Nghĩa: cấp trên — supervisor; Ghi chú: …; 上司に…。; Romaji: …; English: …; Tiếng Việt: …

Scene kinds: a vocabulary card (has "Nghĩa:"), a quiz card (tokens "Thẻ N: word → …",
answers hidden) or a generic title card (first token large, the rest below).
"""
from __future__ import annotations

import hashlib
import io
import re
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .project import Project, ValidationError

MARKER = 'TEXT OVERLAY:'
LABEL_RE = re.compile(r'^(Romaji|Từ gốc|Nghĩa|Ghi chú|English|Tiếng Việt)\s*:\s*(.*)$')
CLOSE_PUNCT = set('。、）」』？！?!.,)')

INK = (31, 42, 68, 255)
INK2 = (55, 65, 81, 255)
MUTED = (107, 114, 128, 255)
ACCENT = (214, 84, 72, 255)
TEAL = (22, 120, 130, 255)
RULE = (226, 228, 233, 255)
NOTE_BG = (254, 243, 222, 255)
NOTE_FG = (146, 64, 14, 255)
PANEL_BG = (255, 255, 255, 238)

# First existing file wins. A project can override with assets/fonts/overlay_*.ttf|otf|ttc.
FONT_CANDIDATES = {
    ('jp', 'bold'): ['C:/Windows/Fonts/YuGothB.ttc', 'C:/Windows/Fonts/meiryob.ttc',
                     '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
                     '/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc'],
    ('jp', 'regular'): ['C:/Windows/Fonts/YuGothM.ttc', 'C:/Windows/Fonts/meiryo.ttc',
                        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
                        '/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc'],
    ('lat', 'bold'): ['C:/Windows/Fonts/segoeuib.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                      '/System/Library/Fonts/Supplemental/Arial Bold.ttf'],
    ('lat', 'regular'): ['C:/Windows/Fonts/segoeui.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                         '/System/Library/Fonts/Supplemental/Arial.ttf'],
    ('lat', 'italic'): ['C:/Windows/Fonts/segoeuii.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf',
                        '/System/Library/Fonts/Supplemental/Arial Italic.ttf'],
}


def split_prompt(prompt: str) -> tuple[str, str | None]:
    """(illustration prompt, overlay text) — image APIs must never see the overlay text."""
    if MARKER not in prompt:
        return prompt, None
    before, after = prompt.split(MARKER, 1)
    return before.strip(), after.strip()


def overlay_tokens(text: str) -> list[str]:
    return [t.strip() for t in text.split('; ') if t.strip()]


def is_jp(ch: str) -> bool:
    o = ord(ch)
    return 0x3000 <= o <= 0x30FF or 0x3400 <= o <= 0x9FFF or 0xFF00 <= o <= 0xFFEF or 0x2190 <= o <= 0x21FF


def has_jp(s: str) -> bool:
    return any(is_jp(c) for c in s)


def parse_word(tokens: list[str]) -> dict[str, Any]:
    info: dict[str, Any] = {'word': '', 'reading': None, 'romaji': None, 'origin': None, 'meaning': None,
                            'notes': [], 'ex': None, 'ex_romaji': None, 'en': None, 'vi': None}
    head = re.match(r'^(.+?)（(.+?)）$', tokens[0])
    info['word'], info['reading'] = (head.group(1), head.group(2)) if head else (tokens[0], None)
    last = None
    for token in tokens[1:]:
        labelled = LABEL_RE.match(token)
        if labelled:
            label, value = labelled.groups()
            if label == 'Romaji':
                info['ex_romaji' if info['ex'] else 'romaji'] = value
            elif label == 'Ghi chú':
                info['notes'].append(value)
            else:
                info[{'Từ gốc': 'origin', 'Nghĩa': 'meaning', 'English': 'en', 'Tiếng Việt': 'vi'}[label]] = value
            last = label
            continue
        if info['ex'] is None and has_jp(token) and token[-1] in '。？?！!':
            info['ex'], last = token, None
        elif last == 'Ghi chú':
            info['notes'][-1] += '; ' + token  # note text that itself contained "; "
        elif info['romaji'] is None and not has_jp(token):
            info['romaji'] = token
        elif info['meaning'] is None and not has_jp(token):
            info['meaning'] = token
        else:
            info['notes'].append(token)
    return info


class _Fonts:
    def __init__(self, project: Project | None):
        self.files: dict[tuple[str, str], str] = {}
        override = (project.folder / 'assets' / 'fonts') if project is not None else None
        for key, candidates in FONT_CANDIDATES.items():
            local = []
            if override is not None and override.is_dir():
                local = sorted(str(p) for p in override.glob(f'overlay_{key[0]}_{key[1]}.*'))
            found = next((c for c in local + candidates if Path(c).is_file()), None)
            if found:
                self.files[key] = found
        if ('lat', 'regular') not in self.files:
            raise ValidationError('No usable font for overlays; add assets/fonts/overlay_lat_regular.ttf')
        self.cache: dict[tuple[str, str, int], ImageFont.FreeTypeFont] = {}
        self.missing_jp = ('jp', 'regular') not in self.files

    def get(self, kind: str, weight: str, size: int) -> ImageFont.FreeTypeFont:
        if kind == 'jp' and weight == 'italic':
            weight = 'regular'
        path = (self.files.get((kind, weight)) or self.files.get((kind, 'regular'))
                or self.files.get(('lat', weight)) or self.files[('lat', 'regular')])
        key = (path, weight, size)
        if key not in self.cache:
            self.cache[key] = ImageFont.truetype(path, size)
        return self.cache[key]


class _Layout:
    def __init__(self, fonts: _Fonts):
        self.fonts = fonts

    def runs(self, s: str) -> list[list[str]]:
        out: list[list[str]] = []
        for ch in s:
            kind = 'jp' if is_jp(ch) else 'lat'
            if out and out[-1][0] == kind:
                out[-1][1] += ch
            else:
                out.append([kind, ch])
        return out

    def width(self, s: str, weight: str, size: int) -> float:
        return sum(self.fonts.get(k, weight, size).getlength(t) for k, t in self.runs(s))

    @staticmethod
    def units(text: str) -> list[str]:
        out, buf = [], ''
        for ch in text:
            if is_jp(ch):
                if buf:
                    out.append(buf)
                    buf = ''
                out.append(ch)
            elif ch == ' ':
                out.append(buf + ch)
                buf = ''
            else:
                buf += ch
        if buf:
            out.append(buf)
        return out

    def wrap(self, text: str, weight: str, size: int, max_width: float) -> list[str]:
        lines, current = [], ''
        for unit in self.units(text):
            if current.strip() and unit[0] not in CLOSE_PUNCT and \
                    self.width(current + unit.rstrip(), weight, size) > max_width:
                lines.append(current.rstrip())
                current = unit.lstrip()
            else:
                current += unit
        if current.strip():
            lines.append(current.rstrip())
        return lines

    def line(self, draw, x, y, s, weight, size, fill):
        base = y + size
        for kind, text in self.runs(s):
            font = self.fonts.get(kind, weight, size)
            draw.text((x, base), text, font=font, fill=fill, anchor='ls')
            x += font.getlength(text)

    def blocks(self, draw, x, y, max_width, blocks, s, paint=True) -> int:
        top = y
        for block in blocks:
            kind = block[0]
            if kind == 'gap':
                y += int(block[1] * s)
            elif kind == 'rule':
                if paint:
                    draw.line([(x, y), (x + max_width, y)], fill=RULE, width=max(1, int(3 * s)))
                y += max(1, int(3 * s))
            elif kind == 'big':
                text, size, color = block[1], int(block[2] * s), block[3]
                while size > 40 * s and self.width(text, 'bold', size) > max_width:
                    size -= 4
                if paint:
                    self.line(draw, x, y, text, 'bold', size, color)
                y += int(size * 1.3)
            elif kind == 'text':
                text, weight, size, color = block[1], block[2], int(block[3] * s), block[4]
                for ln in self.wrap(text, weight, size, max_width):
                    if paint:
                        self.line(draw, x, y, ln, weight, size, color)
                    y += int(size * 1.42)
            elif kind == 'note':
                text, size = block[1], int(block[2] * s)
                pad = int(18 * s)
                lines = self.wrap(text, 'regular', size, max_width - 2 * pad)
                height = len(lines) * int(size * 1.42) + 2 * pad
                if paint:
                    draw.rounded_rectangle([x, y, x + max_width, y + height], radius=int(16 * s), fill=NOTE_BG)
                    yy = y + pad
                    for ln in lines:
                        self.line(draw, x + pad, yy, ln, 'regular', size, NOTE_FG)
                        yy += int(size * 1.42)
                y += height
        return y - top


def _word_blocks(number: int | None, w: dict[str, Any]) -> list[tuple]:
    b: list[tuple] = []
    if number is not None:
        b += [('text', f'TỪ SỐ {number:02d}', 'bold', 30, ACCENT), ('gap', 16)]
    if w['reading']:
        b.append(('text', w['reading'], 'regular', 42, MUTED))
    b.append(('big', w['word'], 150, INK))
    if w['romaji']:
        b.append(('text', w['romaji'], 'italic', 40, MUTED))
    if w['origin']:
        b.append(('text', f"Từ gốc: {w['origin']}", 'regular', 32, MUTED))
    if w['meaning']:
        b += [('gap', 12), ('text', w['meaning'], 'bold', 46, TEAL)]
    if w['ex']:
        b += [('gap', 30), ('rule',), ('gap', 30), ('text', 'VÍ DỤ', 'bold', 26, ACCENT), ('gap', 6),
              ('text', w['ex'], 'regular', 44, INK)]
        if w['ex_romaji']:
            b.append(('text', w['ex_romaji'], 'italic', 30, MUTED))
        b.append(('gap', 14))
        if w['en']:
            b.append(('text', f"EN: {w['en']}", 'regular', 34, INK2))
        if w['vi']:
            b.append(('text', f"VI: {w['vi']}", 'regular', 34, INK2))
    for note in w['notes']:
        b += [('gap', 26), ('note', f'Ghi chú: {note}', 28)]
    return b


def _quiz_blocks(tokens: list[str]) -> list[tuple]:
    b: list[tuple] = [('text', 'ÔN TẬP NHANH', 'bold', 60, ACCENT), ('gap', 10),
                      ('text', 'Bạn còn nhớ nghĩa của các từ này không?', 'regular', 34, INK2), ('gap', 40)]
    for token in tokens:
        quiz = re.match(r'^Thẻ\s*(\d+)\s*:\s*(.+?)\s*→', token)
        if quiz:  # answers stay hidden: they are spoken by the narration
            b += [('text', f'{quiz.group(1)}.  {quiz.group(2)}', 'bold', 72, INK), ('gap', 12)]
    return b


def _generic_blocks(tokens: list[str]) -> list[tuple]:
    b: list[tuple] = []
    for i, token in enumerate(tokens):
        token = token if has_jp(token[-1:]) else token.rstrip('.')
        if i == 0:
            b += [('text', token, 'bold', 64, ACCENT), ('gap', 30)]
        else:
            b += [('text', token, 'bold' if i == 1 else 'regular', 46 if i == 1 else 36, INK), ('gap', 18)]
    return b


def card_blocks(overlay_text: str, narration: str = '') -> list[tuple]:
    tokens = overlay_tokens(overlay_text)
    if not tokens:
        return []
    if any(t.startswith('Nghĩa:') for t in tokens):
        number = re.match(r'\s*Từ số (\d+)', narration or '')
        return _word_blocks(int(number.group(1)) if number else None, parse_word(tokens))
    if tokens[0].startswith('Thẻ'):
        return _quiz_blocks(tokens)
    return _generic_blocks(tokens)


def render_card(blocks: list[tuple], width: int, height: int, fonts: _Fonts) -> Image.Image:
    """Transparent RGBA layer: a rounded white panel (left half, or bottom for portrait)."""
    layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    if not blocks:
        return layer
    landscape = width >= height
    if landscape:
        s = min(width / 1920, height / 1080)
        px, py, pw, ph = int(70 * s), int(70 * s), int(900 * s), height - int(140 * s)
    else:
        s = width / 1080
        pw, ph = width - int(100 * s), int(height * 0.52)
        px, py = int(50 * s), height - ph - int(80 * s)
    pad = int(64 * s)
    shadow = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([px + 6 * s, py + 10 * s, px + pw + 6 * s, py + ph + 10 * s],
                                             radius=int(40 * s), fill=(0, 0, 0, 70))
    layer = Image.alpha_composite(layer, shadow.filter(ImageFilter.GaussianBlur(18 * s)))
    panel = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)
    draw.rounded_rectangle([px, py, px + pw, py + ph], radius=int(40 * s), fill=PANEL_BG)
    engine = _Layout(fonts)
    max_w, max_h = pw - 2 * pad, ph - 2 * pad
    scale = s
    while scale > 0.55 * s and engine.blocks(draw, 0, 0, max_w, blocks, scale, paint=False) > max_h:
        scale -= 0.05 * s
    used = engine.blocks(draw, 0, 0, max_w, blocks, scale, paint=False)
    engine.blocks(draw, px + pad, py + pad + max(0, (max_h - used) // 2), max_w, blocks, scale)
    return Image.alpha_composite(layer, panel)


def generate_overlays(project: Project, *, scene_ids: list[str] | None = None,
                      bind: bool = True) -> dict[str, Any]:
    """Create one PNG per scene that has a TEXT OVERLAY section; optionally bind it."""
    from .agent.script_generator import load_script_draft
    script = load_script_draft(project)
    width, height = int(project.config['output']['width']), int(project.config['output']['height'])
    fonts = _Fonts(project)
    wanted = set(scene_ids) if scene_ids else None
    unknown = (wanted or set()) - {s['scene_id'] for s in script['content']['scenes']}
    if unknown:
        raise ValidationError('Unknown scene(s): ' + ', '.join(sorted(unknown)))
    folder = project.folder / 'assets' / 'overlays'
    folder.mkdir(parents=True, exist_ok=True)
    created, skipped = [], []
    for scene in script['content']['scenes']:
        sid = scene['scene_id']
        if wanted is not None and sid not in wanted:
            continue
        _, text = split_prompt(scene.get('prompt', ''))
        if scene.get('type', 'image') != 'image' or not text:
            skipped.append(sid)
            continue
        image = render_card(card_blocks(text, scene.get('narration', '')), width, height, fonts)
        buffer = io.BytesIO()
        image.save(buffer, format='PNG', optimize=True)
        data = buffer.getvalue()
        path = folder / f'overlay_{sid}_{hashlib.sha256(data).hexdigest()[:12]}.png'
        if not path.exists():
            path.write_bytes(data)
        created.append({'scene_id': sid, 'file': path.relative_to(project.folder).as_posix()})
    bound = []
    if bind and created and (project.folder / 'agent_manifest.json').is_file():
        from .agent.manifest import bind_asset
        for item in created:
            bind_asset(project, item['scene_id'], 'overlay', item['file'])
            bound.append(item['scene_id'])
    return {'project_id': project.project_id, 'created': created, 'skipped': skipped, 'bound': bound,
            'resolution': [width, height],
            'warnings': (['No Japanese font found: CJK characters may not render'] if fonts.missing_jp else [])}


if __name__ == '__main__':  # pragma: no cover - debugging helper
    print('Use: ai-video-editor overlay-generate PROJECT', file=sys.stderr)
