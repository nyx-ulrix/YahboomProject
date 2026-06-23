/**
 * Create backend/.venv (if missing) and pip install requirements.txt.
 */
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const venvDir = path.join(root, 'backend', '.venv');
const isWin = process.platform === 'win32';
const venvPython = isWin
  ? path.join(venvDir, 'Scripts', 'python.exe')
  : path.join(venvDir, 'bin', 'python');

function run(cmd, args, opts = {}) {
  const result = spawnSync(cmd, args, { stdio: 'inherit', ...opts });
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

if (!existsSync(venvPython)) {
  console.log('Creating Python virtual environment at backend/.venv …');
  run('python', ['-m', 'venv', venvDir], { cwd: root });
}

console.log('Installing Python dependencies from requirements.txt …');
run(venvPython, ['-m', 'pip', 'install', '--upgrade', 'pip'], { cwd: root });
run(venvPython, ['-m', 'pip', 'install', '-r', 'requirements.txt'], { cwd: root });
console.log('Backend dependencies ready.');
