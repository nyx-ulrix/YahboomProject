/**
 * Run Flask via backend/.venv — never the system Python interpreter.
 */
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const backendDir = path.join(root, 'backend');
const venvDir = path.join(backendDir, '.venv');
const isWin = process.platform === 'win32';
const venvPython = isWin
  ? path.join(venvDir, 'Scripts', 'python.exe')
  : path.join(venvDir, 'bin', 'python');

if (!existsSync(venvPython)) {
  console.error('Backend venv not found. Run: npm run setup:backend');
  process.exit(1);
}

const result = spawnSync(venvPython, ['main.py'], {
  cwd: backendDir,
  stdio: 'inherit',
});

process.exit(result.status ?? 1);
