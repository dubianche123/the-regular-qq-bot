"""Paid selection and partial sprite builds, using synthetic images only."""
import contextlib
import base64
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pet_sprite_pipeline", ROOT / "tools/pet_sprites.py")
sprites = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sprites)
PIL_AVAILABLE = importlib.util.find_spec("PIL") is not None


class SpritePlanTest(unittest.TestCase):
    def test_plan_counts_only_the_selected_sheets(self):
        args = SimpleNamespace(stage="sheets", output="/unused/source", model=sprites.MODELS[0],
                               only=["gamepad", "popcorn"])
        plan = sprites.plan(args)
        self.assertEqual(plan["requests"], 2)
        self.assertEqual(set(plan["outputs"]), {"gamepad", "popcorn"})
        self.assertEqual(plan["endpoint"], sprites.ENDPOINT)

    def test_library_group_and_action_filters_cannot_expand_paid_scope(self):
        args = SimpleNamespace(stage="library", output="/unused/source", model=sprites.MODELS[0],
                               group="life", only=["life_sleep_back", "motion_edge_peek"])
        self.assertEqual(sprites.plan(args)["outputs"], ["life_sleep_back"])
        args.prompt_file = "/unused/custom-prompt.txt"
        args.only = []
        with self.assertRaisesRegex(ValueError, "one selected action"):
            sprites.plan(args)


class SpriteRequestLedgerTest(unittest.IsolatedAsyncioTestCase):
    async def test_provider_success_and_existing_file_have_different_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            reference = out / "reference.png"
            reference.write_bytes(b"synthetic-reference")
            job = {"name": "example", "refs": [reference], "model": sprites.MODELS[0],
                   "prompt": "synthetic test", "size": "1024x1024"}
            response = SimpleNamespace(status_code=200, json=lambda: {
                "data": [{"b64_json": base64.b64encode(b"synthetic-result").decode()}]})
            http = SimpleNamespace(post=AsyncMock(return_value=response))
            first = await sprites._request(http, "sk-hermetic-test", job, out)
            second = await sprites._request(http, "sk-hermetic-test", job, out)
            self.assertEqual(first["requests_attempted"], 1)
            self.assertEqual(second["skipped"], "exists")
            self.assertEqual(http.post.await_count, 1)
            ledger = (out / "requests.jsonl").read_text()
            rows = [json.loads(line) for line in ledger.splitlines()]
            self.assertEqual([row["event"] for row in rows], ["started", "completed"])
            self.assertEqual(rows[0]["id"], rows[1]["id"])
            self.assertTrue(rows[1]["ok"])
            self.assertNotIn("sk-hermetic-test", ledger)

    async def test_rate_limit_is_recorded_once_without_automatic_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            reference = out / "reference.png"
            reference.write_bytes(b"synthetic-reference")
            job = {"name": "example", "refs": [reference], "model": sprites.MODELS[0],
                   "prompt": "synthetic test", "size": "1024x1024"}
            http = SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(status_code=429)))
            result = await sprites._request(http, "sk-hermetic-test", job, out)
            self.assertEqual(result["status"], 429)
            self.assertEqual(http.post.await_count, 1)
            rows = [json.loads(line) for line in (out / "requests.jsonl").read_text().splitlines()]
            self.assertFalse(rows[-1]["ok"])
            self.assertFalse((out / "example.png").exists())


@unittest.skipUnless(PIL_AVAILABLE, "maintainer sprite pipeline requires Pillow")
class SpriteGenerationSelectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_generation_uses_the_same_selected_scope_as_the_plan(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / ".env").write_text("OPENAI_API_KEY=sk-hermetic-dummy-sprites\n")
            args = SimpleNamespace(stage="sheets", directory=str(home), output=str(home / "source"),
                                   model=sprites.MODELS[0], only=["headphones", "tail_care"], parallel=2)
            request = AsyncMock(return_value={"requests_attempted": 1})
            with patch.object(sprites, "_request", request), contextlib.redirect_stdout(io.StringIO()):
                result = await sprites.generate(args)
            self.assertEqual(result, 0)
            self.assertEqual(request.await_count, 2)
            self.assertEqual({call.args[2]["name"] for call in request.await_args_list},
                             set(sprites.plan(args)["outputs"]))


@unittest.skipUnless(PIL_AVAILABLE, "maintainer sprite pipeline requires Pillow")
class PartialSpriteBuildTest(unittest.TestCase):
    def test_calibration_keeps_head_size_when_arms_change_the_full_height(self):
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as folder:
            source, out = Path(folder) / "source", Path(folder) / "calibrated"
            source.mkdir()
            base = Image.new("RGBA", (512, 512))
            draw = ImageDraw.Draw(base)
            draw.rectangle((205, 300, 305, 492), fill=(90, 140, 110, 255))
            draw.ellipse((170, 140, 340, 300), fill=(220, 200, 240, 255))       # hair
            draw.ellipse((205, 185, 300, 280), fill=(250, 214, 200, 255))       # face
            for x in (225, 262):
                draw.ellipse((x, 215, x + 18, 240), fill=(120, 90, 170, 255))    # eyes
            draw.polygon([(180, 165), (195, 105), (215, 150)], fill=(200, 190, 230, 255))   # horns
            draw.polygon([(295, 150), (318, 105), (330, 165)], fill=(200, 190, 230, 255))
            raised = base.copy()
            ImageDraw.Draw(raised).rectangle((140, 60, 165, 300), fill=(90, 140, 110, 255))
            smaller = Image.new("RGBA", (512, 512))       # same feet, head drawn at 75%
            smaller.alpha_composite(raised.resize((384, 384)), (64, 492 - 369))
            blank = Image.new("RGBA", (512, 512))
            ImageDraw.Draw(blank).rectangle((250, 300, 262, 492), fill=(90, 140, 110, 255))   # a bare prop
            for name, frame in (("idle", base), ("raised", smaller), ("faceless", blank)):
                strip = Image.new("RGBA", (2048, 512))
                for index in range(4):
                    strip.alpha_composite(frame, (index * 512, 0))
                strip.save(source / f"{name}.webp", lossless=True)
            original = {"schema": 2, "frame": [512, 512], "anchor": [256, 492], "figure_height": 320,
                        "actions": {n: {"file": f"{n}.webp", "frames": 4} for n in ("idle", "raised", "faceless")}}
            (source / "sprites.json").write_text(json.dumps(original))
            with contextlib.redirect_stdout(io.StringIO()):
                sprites.calibrate(SimpleNamespace(source=str(source), out=str(out), only=None))
            report = json.loads((out / "calibration-report.json").read_text())
            self.assertAlmostEqual(report["raised"]["relative_head"], 0.75, delta=0.04)
            self.assertIsNone(report["faceless"]["relative_head"])       # nothing to match: left as it was
            self.assertEqual(report["faceless"]["scale"], 1.0)
            with Image.open(out / "idle.webp") as strip:
                matcher = sprites.HeadMatcher(strip.crop((0, 0, 512, 512)).convert("RGBA"))
            with Image.open(out / "raised.webp") as strip:
                score, scale = matcher.measure(strip.crop((0, 0, 512, 512)).convert("RGBA"))
            self.assertGreater(score, 0.5)
            self.assertAlmostEqual(scale, 1.0, delta=0.04)                # after calibration: the idle head size
            self.assertEqual(json.loads((source / "sprites.json").read_text()), original)

    def test_library_frames_share_one_scale_and_keep_edge_grip_alignment(self):
        from PIL import Image, ImageDraw
        from regular.pet_repertoire import ACTIONS

        with tempfile.TemporaryDirectory() as folder:
            source, out = Path(folder) / "source", Path(folder) / "built"
            source.mkdir()
            idle = Image.new("RGBA", (1024, 1024))
            ImageDraw.Draw(idle).rectangle((180, 60, 330, 450), fill=(100, 150, 120, 255))
            idle.save(source / "idle.png")
            sheet = Image.new("RGBA", (1024, 1024))
            draw = ImageDraw.Draw(sheet)
            for index, top in enumerate((70, 90, 110, 90)):
                x, y = index % 2 * 512, index // 2 * 512
                draw.rectangle((x + 60, y + top, x + 300, y + 450), fill=(80, 150, 120, 255))
                draw.rectangle((x + 200, y + 200, x + 204, y + 440), fill=(255, 0, 255, 255))
            sheet.save(source / "synthetic_edge.png")
            args = SimpleNamespace(source=str(source), out=str(out), only=["synthetic_edge"])
            with patch.dict(ACTIONS, {"synthetic_edge": {"scale": 1.0, "grip_x": 204}}), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                sprites.build(args)
            self.assertEqual(json.loads(output.getvalue())["clipped"], [])
            with Image.open(out / "synthetic_edge.webp") as strip:
                widths = []
                for index in range(4):
                    frame = strip.crop((index * 512, 0, (index + 1) * 512, 512)).convert("RGBA")
                    box = frame.getchannel("A").point(lambda a: 255 if a > 40 else 0).getbbox()
                    widths.append(box[2] - box[0])
                    self.assertGreaterEqual(box[0], 5)
                    self.assertGreaterEqual(box[1], 5)
                    self.assertLessEqual(box[2], 507)
                    # The magenta support pole is removed: it would flash while the frame dissolves after she pops out.
                    pole = [(x, y) for y in range(512) for x in range(512)
                            if (lambda p: p[3] > 100 and p[0] > 170 and p[2] > 170 and p[1] < 80)(frame.getpixel((x, y)))]
                    self.assertEqual(pole, [])
                self.assertLessEqual(max(widths) - min(widths), 1)

    def test_new_strips_keep_existing_files_and_manifest_entries(self):
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as folder:
            source, out = Path(folder) / "source", Path(folder) / "built"
            source.mkdir()
            out.mkdir()
            # A tall idle reference establishes scale. Two low, wide figures
            # exercise the new seated/resting sheets without any generated art.
            idle = Image.new("RGBA", (1024, 1024))
            ImageDraw.Draw(idle).rectangle((180, 60, 330, 450), fill=(100, 150, 120, 255))
            idle.save(source / "idle.png")
            for name in ("popcorn", "lie_chin"):
                sheet = Image.new("RGBA", (1024, 1024))
                draw = ImageDraw.Draw(sheet)
                for index in range(4):
                    x, y = (index % 2) * 512, (index // 2) * 512
                    draw.rectangle((x + 105, y + 220, x + 405, y + 450),
                                   fill=(100 + index * 15, 150, 120, 255))
                sheet.save(source / f"{name}.png")
            existing = {"idle": {"file": "idle.webp", "frames": 3},
                        "inspect": {"file": "inspect.webp", "frames": 4}}
            for row in existing.values():
                (out / row["file"]).write_bytes(b"keep-existing-strip")
            (out / "sprites.json").write_text(json.dumps({"actions": existing}))
            args = SimpleNamespace(source=str(source), out=str(out), only=["popcorn", "lie_chin"])

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(sprites.build(args), 0)

            self.assertEqual(json.loads(output.getvalue())["clipped"], [])
            manifest = json.loads((out / "sprites.json").read_text())
            for name, row in existing.items():
                self.assertEqual(manifest["actions"][name], row)
                self.assertEqual((out / row["file"]).read_bytes(), b"keep-existing-strip")
            for name in args.only:
                self.assertEqual(manifest["actions"][name], {"file": f"{name}.webp", "frames": 4})
                with Image.open(out / f"{name}.webp") as strip:
                    self.assertEqual(strip.size, (2048, 512))
                    for index in range(4):
                        frame = strip.crop((index * 512, 0, (index + 1) * 512, 512))
                        self.assertIsNotNone(frame.getchannel("A").getbbox())


if __name__ == "__main__":
    unittest.main()
