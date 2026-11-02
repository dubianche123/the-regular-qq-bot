import { pathToFileURL } from 'node:url';
import { isAbsolute } from 'node:path';
// AMR/SILK is received via stdin and only bounded PCM WAV goes to stdout.
const maxSeconds = Number(process.argv[2]);
const codecPath = process.argv[3];
if (!Number.isFinite(maxSeconds) || maxSeconds <= 0 || maxSeconds > 60 ||
    !codecPath || !isAbsolute(codecPath) || codecPath.length > 4096) process.exit(1);

const chunks = [];
let length = 0;
for await (const chunk of process.stdin) {
  length += chunk.length;
  if (length > 4 * 1024 * 1024) process.exit(1);
  chunks.push(chunk);
}
try {
  const input = Buffer.concat(chunks);
  let sampleRate, pcmBytes;
  if (input.subarray(0, 6).equals(Buffer.from('#!AMR\n')) || input.subarray(0, 9).equals(Buffer.from('#!AMR-WB\n'))) {
    const {default: decode} = await import(pathToFileURL(codecPath).href);
    const result = await decode(input);
    sampleRate = result.sampleRate;
    const pcm = result.channelData[0];
    if (!pcm || ![8000, 16000].includes(sampleRate) || pcm.length > sampleRate * maxSeconds) process.exit(1);
    pcmBytes = Buffer.alloc(pcm.length * 2);
    for (let i = 0; i < pcm.length; i++) {
      const n = Math.max(-1, Math.min(1, pcm[i]));
      pcmBytes.writeInt16LE(n < 0 ? Math.round(n * 32768) : Math.round(n * 32767), i * 2);
    }
  } else if (input.subarray(0, 9).equals(Buffer.from('#!SILK_V3')) || input.subarray(0, 10).equals(Buffer.from('\x02#!SILK_V3'))) {
    const {decode} = await import(pathToFileURL(codecPath).href);
    sampleRate = 24000;
    // The WASM implementation expects Tencent's 0x02 prefix; validated
    // standard SILK_V3 input is converted by adding that byte in memory.
    const silk = input[0] === 2 ? input : Buffer.concat([Buffer.from([2]), input]);
    const result = await decode(silk, sampleRate);
    pcmBytes = Buffer.from(result.data);
    if (!pcmBytes.length || pcmBytes.length % 2 || pcmBytes.length > sampleRate * 2 * maxSeconds) process.exit(1);
  } else {
    process.exit(1);
  }
  const wav = Buffer.alloc(44 + pcmBytes.length);
  wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(sampleRate, 24); wav.writeUInt32LE(sampleRate * 2, 28);
  wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36);
  wav.writeUInt32LE(pcmBytes.length, 40);
  pcmBytes.copy(wav, 44);
  process.stdout.write(wav);
} catch { process.exit(1); }
