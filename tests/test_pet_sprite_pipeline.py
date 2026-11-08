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

    def test_extra_references_survive_style_override(self):
        from regular.pet_repertoire import ACTIONS
        spec = {"style": "idle", "prompt": "action", "extra_refs": ["chair"]}
        args = SimpleNamespace(stage="library", output="/tmp/test-source", model=sprites.MODELS[0],
                               group="form", only=["form_example"], style_reference="/tmp/style.png")
        with patch.dict(ACTIONS, {"form_example": spec}):
            job = sprites._selected_jobs(args)[0]
            self.assertEqual(job["refs"], [Path("/tmp/test-source/base.png"), Path("/tmp/style.png"), Path("/tmp/test-source/chair.png")])
            self.assertEqual(sprites.plan(args)["requests"], 1)

    def test_custom_header_remains_on_prompt_override_and_scope_stays_narrow(self):
        from regular.pet_repertoire import ACTIONS
        with tempfile.TemporaryDirectory() as folder:
            prompt = Path(folder) / "prompt.txt"
            prompt.write_text("new action", encoding="utf-8")
            custom = {"style": "idle", "prompt": "old action", "prompt_header": "DRAGON ONLY"}
            args = SimpleNamespace(stage="library", output=folder, model=sprites.MODELS[0],
                                   group="form", only=["form_dragon"], prompt_file=str(prompt))
            with patch.dict(ACTIONS, {"form_dragon": custom, "form_other": custom}):
                self.assertEqual(sprites.plan(args)["outputs"], ["form_dragon"])
                job = sprites._selected_jobs(args)[0]
                self.assertEqual(job["prompt"], "DRAGON ONLY\nAction: new action")
                args.prompt_file = None
                self.assertEqual(sprites._selected_jobs(args)[0]["prompt"], "DRAGON ONLY\nAction: old action")

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
    def test_reused_rest_starts_with_exact_settled_frame(self):
        from PIL import Image
        from regular.pet_repertoire import ACTIONS

        with tempfile.TemporaryDirectory() as folder:
            source, out = Path(folder) / "source", Path(folder) / "built"
            source.mkdir(); out.mkdir()
            Image.new("RGBA", (1024, 1024), (120, 80, 200, 255)).save(source / "idle.png")
            original = Image.new("RGBA", (2048, 512))
            for i in range(4):
                original.paste((20 * i, 60, 120, 200), (i * 512, 0, (i + 1) * 512, 512))
            original.save(out / "example_settle.webp", "WEBP", lossless=True, exact=True)
            (out / "sprites.json").write_text(json.dumps({"frame": [512, 512], "actions": {"example_settle": {"file": "example_settle.webp", "frames": 4}}, "geometry": {"example_settle": {"bounds": [0, 0, 1, 1]}}}))
            spec = {"scale": 1.0, "reuse_strip_frames": {"strip": "example_settle", "frames": [3, 2, 3, 3]}}
            with patch.dict(ACTIONS, {"example_rest": spec}), contextlib.redirect_stdout(io.StringIO()):
                sprites.build(SimpleNamespace(source=str(source), out=str(out), only=["example_rest"]))
            with Image.open(out / "example_settle.webp") as a, Image.open(out / "example_rest.webp") as b:
                self.assertEqual(a.convert("RGBA").crop((1536, 0, 2048, 512)).tobytes(), b.convert("RGBA").crop((0, 0, 512, 512)).tobytes())

    def test_calibrate_reuses_calibrated_parent_even_when_child_precedes_it(self):
        from PIL import Image, ImageDraw
        from regular.pet_repertoire import ACTIONS

        with tempfile.TemporaryDirectory() as folder:
            source, out = Path(folder) / "source", Path(folder) / "out"
            source.mkdir()
            single = Image.new("RGBA", (512, 512))
            ImageDraw.Draw(single).rectangle((180, 160, 330, 480), fill=(180, 120, 220, 255))
            strip = Image.new("RGBA", (2048, 512))
            for i in range(4):
                frame = single.copy()
                ImageDraw.Draw(frame).rectangle((210 + i * 8, 210, 220 + i * 8, 220), fill=(20, 20, 20, 255))
                strip.paste(frame, (i * 512, 0))
            for name in ("idle", "example_parent", "example_child"):
                strip.save(source / (name + ".webp"), "WEBP", lossless=True, exact=True)
            actions = {name: {"file": name + ".webp", "frames": 4} for name in ("idle", "example_child", "example_parent")}
            (source / "sprites.json").write_text(json.dumps({"frame": [512, 512], "anchor": [256, 492], "actions": actions}))
            child = {"reuse_strip_frames": {"strip": "example_parent", "frames": [3, 2, 3, 3]}}
            matcher = SimpleNamespace(strip_scale=lambda frames: (1.0, [(1.0, 1.0)] * len(frames)))
            with patch.dict(ACTIONS, {"example_child": child}), patch.object(sprites, "HeadMatcher", return_value=matcher), contextlib.redirect_stdout(io.StringIO()):
                sprites.calibrate(SimpleNamespace(source=str(source), out=str(out), only=None))
            with Image.open(out / "example_parent.webp") as a, Image.open(out / "example_child.webp") as b:
                self.assertEqual(a.convert("RGBA").crop((1536, 0, 2048, 512)).tobytes(), b.convert("RGBA").crop((0, 0, 512, 512)).tobytes())
            result = json.loads((out / "sprites.json").read_text())
            self.assertEqual(result["geometry"]["example_child"], result["geometry"]["example_parent"])

    def test_performance_camera_zoom_is_removed_without_using_prop_bounds(self):
        from PIL import Image, ImageDraw
        from regular.pet_repertoire import ACTIONS

        with tempfile.TemporaryDirectory() as folder:
            source, out = Path(folder) / "source", Path(folder) / "built"
            source.mkdir(); out.mkdir()
            frame = Image.new("RGBA", (512, 512))
            draw = ImageDraw.Draw(frame)
            draw.rectangle((205, 285, 305, 470), fill=(90, 140, 110, 255))
            draw.ellipse((165, 120, 345, 300), fill=(220, 200, 240, 255))
            draw.ellipse((200, 165, 310, 275), fill=(250, 214, 200, 255))
            for x in (222, 270):
                draw.ellipse((x, 208, x + 18, 235), fill=(120, 90, 170, 255))
            draw.polygon([(180, 155), (195, 80), (218, 150)], fill=(200, 190, 230, 255))
            draw.polygon([(295, 150), (320, 80), (332, 160)], fill=(200, 190, 230, 255))
            sheet = Image.new("RGBA", (1024, 1024))
            for i in range(4):
                sheet.alpha_composite(frame, (i % 2 * 512, i // 2 * 512))
            sheet.save(source / "idle.png")
            for face in sprites.FACES:
                sheet.save(source / f"{face}.png")
            with contextlib.redirect_stdout(io.StringIO()):
                sprites.build(SimpleNamespace(source=str(source), out=str(out), only=["idle"]))
            changed = Image.new("RGBA", (1024, 1024))
            for i, factor in enumerate((.75, .85, .95, 1.0)):
                smaller = frame.resize((round(512 * factor), round(512 * factor)))
                cell = Image.new("RGBA", (512, 512))
                cell.alpha_composite(smaller, (round(256 * (1-factor)), round(470 * (1-factor))))
                changed.alpha_composite(cell, (i % 2 * 512, i // 2 * 512))
            changed.save(source / "synthetic_performance.png")
            with patch.dict(ACTIONS, {"synthetic_performance": {"scale": 1.0, "frame_calibration": True}}), \
                    contextlib.redirect_stdout(io.StringIO()):
                sprites.build(SimpleNamespace(source=str(source), out=str(out), only=["synthetic_performance"]))
            with Image.open(out / "idle.webp") as strip:
                matcher = sprites.HeadMatcher(strip.crop((0, 0, 512, 512)))
            with Image.open(out / "synthetic_performance.webp") as strip:
                for i in range(4):
                    confidence, relative = matcher.measure(strip.crop((i*512, 0, (i+1)*512, 512)))
                    self.assertGreater(confidence, .5)
                    self.assertAlmostEqual(relative, 1.0, delta=.045)

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
            original["geometry"] = {"raised": {"rest_x": -25, "rest_y": -100}}
            (source / "sprites.json").write_text(json.dumps(original))
            with contextlib.redirect_stdout(io.StringIO()):
                sprites.calibrate(SimpleNamespace(source=str(source), out=str(out), only=None))
            report = json.loads((out / "calibration-report.json").read_text())
            geometry = json.loads((out / "sprites.json").read_text())["geometry"]["raised"]
            self.assertAlmostEqual(geometry["rest_x"], -25 * report["raised"]["scale"], delta=.02)
            self.assertAlmostEqual(geometry["rest_y"], -100 * report["raised"]["scale"], delta=.02)
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

    def test_supported_sleep_keeps_the_contact_plane_fixed_across_entry_frames(self):
        from PIL import Image, ImageDraw
        from regular.pet_repertoire import ACTIONS

        with tempfile.TemporaryDirectory() as folder:
            source, out = Path(folder) / "source", Path(folder) / "built"
            source.mkdir()
            idle = Image.new("RGBA", (1024, 1024))
            ImageDraw.Draw(idle).rectangle((180, 60, 330, 450), fill=(100, 150, 120, 255))
            idle.save(source / "idle.png")
            sheet = Image.new("RGBA", (1024, 1024))
            for i, feet in enumerate((410, 430, 450, 470)):
                x, y = i % 2 * 512, i // 2 * 512
                draw = ImageDraw.Draw(sheet)
                draw.rectangle((x + 180, y + 80, x + 330, y + feet), fill=(90, 150, 120, 255))
                draw.rectangle((x + 190, y + 300, x + 320, y + 303), fill=(30, 50, 240, 255))
            sheet.save(source / "supported_sleep.png")
            args = SimpleNamespace(source=str(source), out=str(out), only=["supported_sleep"])
            with patch.dict(ACTIONS, {"supported_sleep": {"scale": 1.0, "rest_y": 300}}), contextlib.redirect_stdout(io.StringIO()):
                sprites.build(args)
            geometry = json.loads((out / "sprites.json").read_text())["geometry"]["supported_sleep"]
            expected = sprites.ANCHOR[1] + geometry["rest_y"]
            with Image.open(out / "supported_sleep.webp") as strip:
                for i in range(4):
                    frame = strip.crop((i * 512, 0, (i + 1) * 512, 512)).convert("RGBA")
                    rows = [y for y in range(512) if any(
                        (lambda p: p[2] > 180 and p[0] < 80 and p[1] < 100 and p[3] > 150)(frame.getpixel((x, y)))
                        for x in range(512))]
                    self.assertTrue(rows)
                    self.assertAlmostEqual(min(rows), expected, delta=2)

    def test_source_cell_selection_excludes_rejected_art_from_the_built_strip(self):
        from PIL import Image, ImageDraw
        from regular.pet_repertoire import ACTIONS

        with tempfile.TemporaryDirectory() as folder:
            source, out = Path(folder) / "source", Path(folder) / "built"
            source.mkdir()
            sheet = Image.new("RGBA", (1024, 1024))
            colors = [(240, 20, 20, 255), (20, 240, 20, 255),
                      (20, 20, 240, 255), (240, 240, 20, 255)]
            for i, color in enumerate(colors):
                x, y = i % 2 * 512, i // 2 * 512
                ImageDraw.Draw(sheet).rectangle((x + 180, y + 60, x + 330, y + 450), fill=color)
            sheet.save(source / "idle.png")
            sheet.save(source / "reviewed_cast.png")
            args = SimpleNamespace(source=str(source), out=str(out), only=["reviewed_cast"])
            action = {"scale": 1.0, "head_calibration": False, "cells": [0, 1, 3, 3]}
            with patch.dict(ACTIONS, {"reviewed_cast": action}), contextlib.redirect_stdout(io.StringIO()):
                sprites.build(args)
            manifest = json.loads((out / "sprites.json").read_text())
            self.assertEqual(manifest["actions"]["reviewed_cast"]["frames"], 4)
            with Image.open(out / "reviewed_cast.webp") as strip:
                frames = [strip.crop((i * 512, 0, (i + 1) * 512, 512)).convert("RGBA") for i in range(4)]
                for frame, expected in zip(frames, (colors[0], colors[1], colors[3], colors[3])):
                    sample = frame.getpixel((256, 300))
                    self.assertEqual(sample[3], 255)
                    for actual, channel in zip(sample[:3], expected[:3]):
                        self.assertAlmostEqual(actual, channel, delta=6)
                self.assertEqual(frames[2].tobytes(), frames[3].tobytes())
            for invalid in ([], [0, 1, 3], [0, 1, 2, 4], [0, 1, 2, -1], [0, 1, 2, True], "0123"):
                with self.subTest(cells=invalid), patch.dict(ACTIONS, {"reviewed_cast": dict(action, cells=invalid)}):
                    with self.assertRaisesRegex(ValueError, "four source cells"):
                        sprites.build(args)

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
