// All paths are supplied by the trusted caller. No network or QQ operations.
import { readFile, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
const [wavPath, silkPath, entry, limit] = process.argv.slice(2);
const { encode, isSilk, getDuration } = await import(pathToFileURL(entry).href);
const wav = await readFile(wavPath);
const result = await encode(wav, 0);
const ms = getDuration(result.data);
if (!result.data.length || !isSilk(result.data) || !Number.isFinite(ms) || ms <= 0 || ms > Number(limit)) {
  throw new Error('invalid SILK format or duration');
}
await writeFile(silkPath, result.data, { mode: 0o600, flag: 'wx' });
