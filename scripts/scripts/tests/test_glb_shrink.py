"""Tests for glb_shrink.py: the Raspberry Pi gets the full model, the 50 MB cloud a smaller copy.

The shrink tests run the real Node tool (glb_tools/shrink.mjs) on a generated dense mesh and are
skipped when Node.js or its packages (cd glb_tools && npm ci) are missing.

Run from scripts/scripts:  python -m unittest discover -s tests -v
"""
import json
import math
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import glb_shrink  # noqa: E402

TOOLS_READY = glb_shrink.node_binary() is not None and os.path.isdir(
    os.path.join(glb_shrink.HERE, "glb_tools", "node_modules"))


def dense_grid_glb(n=300):
    """A wavy n x n grid (2 n^2 triangles) with positions, normals and UVs: a stand-in for a Tripo mesh."""
    positions, normals, uvs = bytearray(), bytearray(), bytearray()
    for j in range(n + 1):
        for i in range(n + 1):
            x, y = i / n, j / n
            positions += struct.pack("<3f", x, y, 0.05 * math.sin(12 * x) * math.cos(9 * y))
            normals += struct.pack("<3f", 0, 0, 1)
            uvs += struct.pack("<2f", x, y)
    indices = bytearray()
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            indices += struct.pack("<6I", a, a + 1, a + n + 1, a + 1, a + n + 2, a + n + 1)
    blobs = [positions, normals, uvs, indices]
    offsets, binary = [], bytearray()
    for blob in blobs:
        offsets.append(len(binary))
        binary += blob
    count = (n + 1) ** 2
    gltf = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": offsets[k], "byteLength": len(blobs[k])} for k in range(4)],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": count, "type": "VEC3", "min": [0, 0, -0.05], "max": [1, 1, 0.05]},
            {"bufferView": 1, "componentType": 5126, "count": count, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5126, "count": count, "type": "VEC2"},
            {"bufferView": 3, "componentType": 5125, "count": len(indices) // 4, "type": "SCALAR"},
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}, "indices": 3}]}],
        "nodes": [{"mesh": 0}], "scenes": [{"nodes": [0]}], "scene": 0,
    }
    js = json.dumps(gltf).encode()
    js += b" " * (-len(js) % 4)
    binary += b"\0" * (-len(binary) % 4)
    total = 12 + 8 + len(js) + 8 + len(binary)
    return (b"glTF" + struct.pack("<II", 2, total) + struct.pack("<I4s", len(js), b"JSON") + js
            + struct.pack("<I4s", len(binary), b"BIN\0") + bytes(binary))


def gltf_json(glb):
    length = struct.unpack_from("<I", glb, 12)[0]
    return json.loads(glb[20:20 + length])


@unittest.skipUnless(TOOLS_READY, "Node.js or glb_tools/node_modules missing (cd glb_tools && npm ci)")
class ShrinkTests(unittest.TestCase):

    def test_large_model_is_reduced_under_the_limit_and_stays_plain(self):
        glb = dense_grid_glb()
        limit = len(glb) // 2
        logs = []
        smaller = glb_shrink.shrink_glb(glb, limit, log=logs.append)
        self.assertIsNotNone(smaller, logs)
        self.assertLessEqual(len(smaller), limit)
        self.assertEqual(smaller[:4], b"glTF")
        doc = gltf_json(smaller)
        self.assertFalse(doc.get("extensionsRequired"), "the app's viewer needs a plain GLB")
        faces = sum(doc["accessors"][p["indices"]]["count"] // 3 for m in doc["meshes"] for p in m["primitives"])
        self.assertLess(faces, 2 * 300 * 300)
        self.assertTrue(any("Копия для облака" in line for line in logs))

    def test_model_that_fits_is_returned_unchanged(self):
        glb = dense_grid_glb(20)
        self.assertIs(glb_shrink.shrink_glb(glb, len(glb) + 1), glb)

    def test_impossible_limit_gives_none(self):
        logs = []
        self.assertIsNone(glb_shrink.shrink_glb(dense_grid_glb(50), 200, log=logs.append))
        self.assertTrue(logs)


class ModelForServerTests(unittest.TestCase):
    BIG = b"x" * (glb_shrink.CLOUD_FILE_LIMIT + 1)

    def test_pi_always_gets_the_full_model(self):
        self.assertIs(glb_shrink.model_for_server("Raspberry Pi", self.BIG, {}, shrink=lambda g: b"small"), self.BIG)

    def test_cloud_gets_the_smaller_copy_made_once(self):
        calls = []
        shrink = lambda g: calls.append(g) or b"small"  # noqa: E731
        cache = {}
        self.assertEqual(glb_shrink.model_for_server("Supabase cloud", self.BIG, cache, shrink=shrink), b"small")
        self.assertEqual(glb_shrink.model_for_server("Supabase cloud", self.BIG, cache, shrink=shrink), b"small")
        self.assertEqual(len(calls), 1)

    def test_small_model_and_no_model_pass_through(self):
        self.assertEqual(glb_shrink.model_for_server("Supabase cloud", b"tiny", {}, shrink=lambda g: 1/0), b"tiny")
        self.assertIsNone(glb_shrink.model_for_server("Supabase cloud", None, {}))

    def test_failed_shrink_means_no_cloud_model(self):
        self.assertIsNone(glb_shrink.model_for_server("Supabase cloud", self.BIG, {}, shrink=lambda g: None))


if __name__ == "__main__":
    unittest.main()
