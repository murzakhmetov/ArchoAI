// Makes a smaller copy of a GLB 3D model that fits a file size limit, for servers that refuse large
// files (the Supabase cloud: 50 MB per file). The Raspberry Pi keeps the full-quality original.
//
//   node shrink.mjs <input.glb> <output.glb> <max bytes>
//
// The mesh is simplified with the meshoptimizer simplifier (vertices welded first, borders and UVs
// kept) until the written file is under the limit; textures are kept as they are. The output is a
// plain glTF binary (no meshopt / quantization extensions), which the app's 3D viewer can load.
// Prints one JSON line: {"inputBytes", "outputBytes", "inputFaces", "outputFaces", "ratio"}.
// Exit code 2 when the limit cannot be reached (textures alone are too large).
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { dequantize, prune, simplify, weld } from '@gltf-transform/functions';
import { MeshoptDecoder, MeshoptSimplifier } from 'meshoptimizer';
import { statSync, writeFileSync } from 'node:fs';

const [input, output, maxArg] = process.argv.slice(2);
const maxBytes = Number(maxArg);
if (!input || !output || !Number.isFinite(maxBytes) || maxBytes <= 0) {
  console.error('usage: node shrink.mjs <input.glb> <output.glb> <max bytes>');
  process.exit(64);
}

await Promise.all([MeshoptDecoder.ready, MeshoptSimplifier.ready]);
const io = new NodeIO()
  .registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ 'meshopt.decoder': MeshoptDecoder });

const PLAIN = ['EXT_meshopt_compression', 'KHR_meshopt_compression', 'KHR_mesh_quantization'];

async function load() {
  const doc = await io.read(input);
  for (const ext of doc.getRoot().listExtensionsUsed()) {
    if (PLAIN.includes(ext.extensionName)) ext.dispose();
  }
  await doc.transform(dequantize());
  return doc;
}

function faceCount(doc) {
  let faces = 0;
  for (const mesh of doc.getRoot().listMeshes()) {
    for (const prim of mesh.listPrimitives()) {
      const indices = prim.getIndices();
      faces += indices ? indices.getCount() / 3 : (prim.getAttribute('POSITION')?.getCount() ?? 0) / 3;
    }
  }
  return Math.round(faces);
}

function textureBytes(doc) {
  return doc.getRoot().listTextures().reduce((sum, t) => sum + (t.getImage()?.byteLength ?? 0), 0);
}

const inputBytes = statSync(input).size;
const original = await load();
const inputFaces = faceCount(original);
const textures = textureBytes(original);
const plain = await io.writeBinary(original);

let result = { bytes: plain, faces: inputFaces, ratio: 1 };
if (plain.byteLength > maxBytes) {
  if (textures >= maxBytes * 0.9) {
    console.error(`textures alone are ${textures} bytes: no geometry simplification can reach ${maxBytes}`);
    process.exit(2);
  }
  // First guess from the geometry share of the file, then tighten until the file fits.
  const geometry = plain.byteLength - textures;
  let ratio = Math.min(0.95, ((maxBytes * 0.92) - textures) / geometry);
  for (let attempt = 0; attempt < 8 && ratio > 0.01; attempt++) {
    const doc = await load();
    await doc.transform(
      weld(),
      simplify({ simplifier: MeshoptSimplifier, ratio, error: 0.01, lockBorder: true }),
      prune(),
    );
    const bytes = await io.writeBinary(doc);
    result = { bytes, faces: faceCount(doc), ratio };
    if (bytes.byteLength <= maxBytes) break;
    ratio *= 0.85 * (maxBytes / bytes.byteLength);
  }
  if (result.bytes.byteLength > maxBytes) {
    console.error(`could not get under ${maxBytes} bytes (last try ${result.bytes.byteLength})`);
    process.exit(2);
  }
}

writeFileSync(output, result.bytes);
console.log(JSON.stringify({
  inputBytes, outputBytes: result.bytes.byteLength, inputFaces, outputFaces: result.faces,
  ratio: Number(result.ratio.toFixed(3)),
}));
