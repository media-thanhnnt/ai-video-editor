import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({plugins:[react()],base:'/',server:{port:5173,proxy:{'/api':'http://127.0.0.1:8765','/script-editor':'http://127.0.0.1:8765'}},build:{outDir:'../src/ai_video_editor/api/static',emptyOutDir:false}});
