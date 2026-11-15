import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const [bundle, language] = process.argv.slice(2);
if (!['c', 'cpp'].includes(language)) throw new Error('Unsupported language');
const name = language === 'cpp' ? 'main.cpp' : 'main.c';
const compiler = language === 'cpp' ? 'clang++' : 'clang';
const stackSize = 4 * 1024 * 1024;
const maxModule = 8 * 1024 * 1024;
const maxDiagnostics = 4096;
const source = readFileSync(0);
let diagnostics = Buffer.alloc(0);

const { runClang, runLLVM, Exit } = await import(pathToFileURL(bundle).href);
const capture = bytes => {
  if (bytes && diagnostics.length < maxDiagnostics) {
    diagnostics = Buffer.concat([diagnostics, bytes.subarray(0, maxDiagnostics - diagnostics.length)]);
  }
};

try {
  // Load the local assets before runClang calls the driver with its default progress logger.
  await runLLVM([compiler, '--version'], {}, { stdout: () => {}, stderr: capture, fetchProgress: () => {} });
  const files = await runClang([compiler, '-O1', '-fno-exceptions', `-Wl,-z,stack-size=${stackSize}`, '-o', 'program.wasm', name], { [name]: source },
    { stdout: capture, stderr: capture, fetchProgress: () => {} });
  const module = files?.['program.wasm'];
  if (!(module instanceof Uint8Array) || module.length > maxModule) {
    process.stdout.write(JSON.stringify({ status: 'limit', diagnostics: diagnostics.toString('utf8') }));
  } else {
    process.stdout.write(JSON.stringify({ status: 'ok', module: Buffer.from(module).toString('base64') }));
  }
} catch (error) {
  if (!(error instanceof Exit)) throw error;
  process.stdout.write(JSON.stringify({ status: 'failed', diagnostics: diagnostics.toString('utf8') }));
}
