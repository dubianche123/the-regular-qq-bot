#!/usr/bin/env python3
"""Maintainer-only static mascot assets; an API plan is the default.

One official Images edit request per selected item, with no retries.
Swift composites only the authorized edit regions back into the original PNG.
No bot/runtime imports, QQ traffic, Python image editing or runtime dependencies.

After visual selection, put the chosen 1024x1536 PNGs in a separate directory:
  python tools/generate_mascot_poses.py --output /tmp/selected-mascot \
    --actions blink wave wave-mid welcome knowledge checkup \
    --export-runtime regular/assets
This offline macOS export uses Swift for two small lower-limb mesh poses and
sips for identical 512x768 canvases, then writes the native action manifest.
The original mascot.png is never overwritten. Review the action sequence at
its actual display size before publishing; the API cannot guarantee a pose.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile

from dotenv import dotenv_values
import httpx

ENDPOINT = "https://api.openai.com/v1/images/edits"
MODEL = "gpt-image-2.6-flare"
SIZE = (1024, 1536)
EYES = [(458, 270, 58, 35), (563, 238, 52, 35)]
MOUTH = (530, 304, 32, 28)
IDENTITY = """Use case: identity-preserve, precise localized expression edit.
Image 1 is the EXACT edit target: an original illustrated adult dragon woman.
The attached alpha mask defines the only editable pixels: two small eye areas,
and, only when included in the mask, one small mouth area. Opaque mask pixels
are protected. Return ONE static PNG, not a GIF, animation, contact sheet or grid.
Preserve the exact original pose, head tilt, camera, framing, 1024x1536 canvas,
character placement and size, face silhouette, hair, horns, costume, hands,
legs, cushion, tail, linework, color palette and transparent background.
Do not redesign or rescale her. Do not shift the face or rotate any body part.
Only change facial expression inside the transparent mask. No text or effects.
"""
ACTIONS = {
    "wave": "ONLY the screen-left waving arm and hand change inside the mask. Her upper arm/shoulder retains its exact position. Bend her elbow and move the forearm outwards to the LEFT about 45 pixels: the open palm should now be near x=245,y=345, with the cuff and sleeve following naturally. Tilt the open hand 15 degrees outward, preserving five natural fingers and hand scale. Keep the same sage knitted sleeve. This is the outward key pose of a friendly arm wave, with a visibly changed forearm, not merely fingers or whole-body motion. Do not change face, hair, scarf, torso, legs, or anything outside the mask. Clear away the old arm silhouette to transparent pixels where it moved.",
    "wave-mid": "Edit ONLY the waving hand, wrist and the upper half of the forearm inside this SMALL mask. The shoulder, upper arm, elbow, wing and the lower sleeve are PROTECTED, not editable. Keep the original knitted cuff band and cable-knit pattern without adding buttons, folds, borders or costume details. Move the palm just 18 pixels LEFT, to center around x=282,y=340, and tilt the open hand 6 degrees outward to the left. The hand stays at the same height and scale, with five natural fingers. The cuff follows the wrist about 12 pixels left, blending into the untouched lower sleeve at y=480. This is just a small half-way hand/forearm wave, not lowering the arm or changing elbow pose. No head/hair/face change. Remove the old hand silhouette to transparency where it moved. Single PNG.",
    "blink": "Both eyes are FULLY CLOSED in a natural relaxed blink: no visible iris or pupil, just gently curved closed eyelids with the original eyelash style and head tilt. Preserve her original smiling OPEN MOUTH exactly: the mouth is outside this mask. Do not move brows or hair outside the eye regions.",
    "thinking": "Her eyes glance slightly upward thoughtfully, with a subtle curious eyebrow expression, and her mouth becomes a small softly closed thoughtful smile. Only eye/brow and mouth details change. The body, hands, hair and head angle remain EXACTLY as in Image 1.",
    "celebrate": "Her eyes become happy closed crescents and her mouth shows a cheerful delighted smile, contained in the existing small mouth area. Only eye/brow and mouth details change. No movement, confetti, extra symbols or changes to her original waving hand.",
    "sleeping": "Her eyelids are softly fully closed, eyebrows relaxed, and her mouth becomes a tiny peaceful closed smile. Keep her head in precisely its original position and angle. Only eye/brow and mouth details change. No Zzz, symbols, new objects or body movement.",
}
SCENES = {
    "welcome": "She sits comfortably on a low moss-green cushion, holding a small ceramic teacup in both hands with a warm welcoming smile, her lavender dragon tail curled comfortably beside her. Relaxed three-quarter seated full-body pose, sneakers visible. No speech-bubble cushion or waving hand. The feeling is a familiar friend waiting to chat.",
    "knowledge": "She is standing, thoughtfully organizing an open cream notebook and two small green index cards in her hands, smiling with gentle curiosity. A small pencil rests behind one ear without disturbing her flower hair ribbon. Clear different arm pose: one hand supports the notebook, the other turns a page. Full body, exact same character design and clothing, no speech-bubble cushion.",
    "checkup": "She leans slightly forward with a curious friendly expression, holding a small magnifying glass in one hand and a cream checklist clipboard in the other, as if carefully checking how her group friend is doing. The clipboard has only three simple sage checkmark shapes, no written words. Her dragon tail curves to balance her stance. Full body, exact same clothing and character, no speech-bubble cushion.",
}
SCENE_IDENTITY = """Use case: identity-preserve, original mascot illustration.
Image 1 is the CHARACTER DESIGN REFERENCE. Create ONE new static full-body
illustration of this exact original adult dragon woman in a different pose.
Lock her adult proportions and exact facial identity: short silver-white bob,
lavender eyes, small pearl horns, green ribbon with an ivory flower, lavender
scarf with flower clasp, oversized sage cable-knit cardigan, ivory embroidered
dress, white sage-accented sneakers, small lavender wings and lavender dragon
tail. Preserve the original delicate manga linework, soft muted colors and warm
personality. Do not turn her into a chibi, child, long-haired fantasy princess,
or a different character. Keep her fully clothed, one character, no extra limbs.
Scene: genuinely transparent background, no cast ground shadow, no environment.
Canvas 1024x1536. Center the complete character and prop with generous margin,
keep horns, wings, tail, hands and both shoes fully in frame. No text or logos.
"""


def png_size(raw):
    if raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
        raise ValueError("Expected PNG output")
    return struct.unpack(">II", raw[16:24])


def swift_tool():
    source = Path(__file__).with_name("mascot_expression.swift")
    cache = Path("/private/tmp/regular-mascot-swift")
    cache.mkdir(parents=True, exist_ok=True)
    binary = cache / "mascot-expression"
    env = dict(os.environ, CLANG_MODULE_CACHE_PATH=str(cache), SWIFT_MODULECACHE_PATH=str(cache))
    if not binary.exists() or source.stat().st_mtime_ns > binary.stat().st_mtime_ns:
        subprocess.run(["xcrun", "swiftc", "-module-cache-path", str(cache), str(source),
                        "-o", str(binary)], check=True, env=env)
    return binary


def export_runtime(args):
    """Export an explicitly curated candidate directory without any API call."""
    helper = swift_tool()
    target = args.export_runtime
    selected = []
    for action in args.actions:
        candidate = args.output / f"mascot-{action}.png"
        if not candidate.is_file() or png_size(candidate.read_bytes()) != SIZE:
            raise ValueError(f"Missing selected 1024x1536 candidate: {candidate.name}")
        selected.append((candidate, candidate.name))
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="regular-mascot-export-") as temporary:
        temporary = Path(temporary)
        for name, amount in (("drag-mid", "0.5"), ("drag", "1")):
            candidate = temporary / f"mascot-{name}.png"
            subprocess.run([str(helper), "deform", str(args.reference), str(candidate), amount], check=True)
            selected.append((candidate, candidate.name))
        selected.append((args.reference, "mascot-idle.png"))
        for candidate, name in selected:
            subprocess.run(["sips", "-z", "768", "512", str(candidate), "--out", str(target / name)],
                           check=True, stdout=subprocess.DEVNULL)
    idle = "mascot-idle.png"
    def clip(frames, fps):
        return {"frames": frames, "fps": fps, "loop": False}
    actions = {
        "idle": clip([idle], 1),
        "drag": clip(["mascot-drag-mid.png", "mascot-drag.png"], 8),
        "land": clip(["mascot-drag.png", "mascot-drag-mid.png", idle], 8),
    }
    if "blink" in args.actions:
        actions["blink"] = clip([idle, "mascot-blink.png", idle], 9)
    if "wave" in args.actions:
        mid = ["mascot-wave-mid.png"] if "wave-mid" in args.actions else []
        actions["wave"] = clip([idle, *mid, "mascot-wave.png", *mid, idle], 7)
    (target / "pet-actions.json").write_text(json.dumps(
        {"schema": 1, "canvas": [512, 768], "actions": actions}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"api_requests": 0, "directory": str(target),
                      "pngs": [{"name": name, "bytes": (target / name).stat().st_size}
                               for _, name in selected]}, ensure_ascii=False))
    return 0


def plan(args):
    return {"requests": len(args.actions), "destination": ENDPOINT, "model": args.model,
            "actions": args.actions, "chat_content": False,
            "reference_image_upload": args.reference.name, "format": "png",
            "size": "1024x1536", "editable_eye_ellipses": EYES,
            "editable_mouth_ellipse": MOUTH,
            "mask_geometry_source": "tools/mascot_expression.swift",
            "mouth_in_blink_mask": False,
            "original_alpha_preserved": not any(x in ("wave", "wave-mid", "drag") or x in SCENES for x in args.actions),
            "outside_edit_regions_pixel_identical": not any(x in SCENES for x in args.actions)}


async def generate(args):
    reference = args.reference.read_bytes()
    if png_size(reference) != SIZE:
        raise ValueError("The original PNG must be 1024x1536")
    if args.directory is None:
        raise ValueError("A private instance directory is required for paid generation")
    key = dotenv_values(args.directory / ".env", interpolate=False).get("OPENAI_API_KEY")
    if not key:
        raise ValueError("Configure OPENAI_API_KEY in the private instance .env")
    args.output.mkdir(parents=True, exist_ok=True)
    for action in args.actions:
        if any((args.output / name).exists() for name in
               (f"mascot-{action}.png", f"mascot-{action}.raw.png")):
            raise ValueError(f"Candidate already exists for {action}; choose a fresh output directory")
    helper = swift_tool()
    transport = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
    async with httpx.AsyncClient(transport=transport, timeout=300,
                                trust_env=False, follow_redirects=False) as http:
        for action in args.actions:
            mask = args.output / f"mascot-{action}.mask.png"
            raw_target = args.output / f"mascot-{action}.raw.png"
            target = args.output / f"mascot-{action}.png"
            region = action
            scene = action in SCENES
            if not scene:
                subprocess.run([str(helper), "mask", str(args.reference), str(mask), region], check=True)
            identity = IDENTITY
            if action in ("wave", "wave-mid", "drag"):
                identity = """Use case: identity-preserve, precise localized limb pose edit.
Image 1 is the EXACT edit target, an original illustrated adult dragon woman.
The attached alpha mask defines the ONLY editable pixels. Opaque mask pixels
are protected. Return ONE static transparent PNG, not a GIF, grid or sheet.
Preserve exact 1024x1536 canvas, head/face/hair/horns, camera, body scale,
torso position, costume, soft linework and colors. Do NOT redesign her.
Only change the named limbs within the transparent mask. Keep the rest exact.
No text, motion lines, glow, shadow, effects or new objects.
"""
            prompt = (SCENE_IDENTITY + "\nNew pose: " + SCENES[action]) if scene else identity + "\nPose: " + ACTIONS[action]
            uploads = {"image": (args.reference.name, reference, "image/png")}
            if not scene:
                uploads["mask"] = (mask.name, mask.read_bytes(), "image/png")
            try:
                response = await http.post(ENDPOINT,
                    headers={"Authorization": "Bearer " + key},
                    files=uploads,
                    data={"model": args.model, "prompt": prompt, "size": "1024x1536",
                          "quality": "medium", "background": "transparent",
                          "output_format": "png", "complexity": "medium", "n": "1"})
                if response.status_code != 200:
                    print(json.dumps({"action": action, "status": response.status_code,
                                      "requests_attempted": 1}), flush=True)
                    return 1
                result = response.json()
                raw = base64.b64decode(result["data"][0]["b64_json"], validate=True)
                if png_size(raw) != SIZE:
                    raise ValueError("The edited PNG changed canvas size")
            except Exception as error:
                # Never print HTTP response text, raw exception messages or credentials.
                print(json.dumps({"action": action, "error": type(error).__name__,
                                  "requests_attempted": 1}), flush=True)
                return 1
            raw_target.write_bytes(raw)
            if scene:
                target.write_bytes(raw)
            else:
                subprocess.run([str(helper), "composite", str(args.reference), str(raw_target),
                                str(target), region], check=True)
            metadata = {**plan(args), "action": action, "prompt": prompt,
                        "requests_attempted": 1, "raw_bytes": len(raw),
                        "candidate": target.name}
            (args.output / f"mascot-{action}.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"action": action, "model": args.model,
                              "file": str(target), "requests_attempted": 1}), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--output", type=Path, default=Path("/private/tmp/regular-mascot-keyframes"))
    parser.add_argument("--reference", type=Path,
                        default=Path(__file__).resolve().parents[1] / "regular/assets/mascot.png")
    parser.add_argument("--actions", nargs="+", choices=[*ACTIONS, *SCENES], default=["blink"])
    parser.add_argument("--model", choices=[MODEL, "gpt-image-2.6-sunburst"], default=MODEL)
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--export-runtime", type=Path,
                        help="Offline: export curated --output candidates and a PNG action manifest (macOS).")
    args = parser.parse_args()
    if len(set(args.actions)) != len(args.actions):
        parser.error("Choose each expression once")
    if args.export_runtime is not None:
        if args.allow_paid:
            parser.error("Offline export cannot be combined with --allow-paid")
        return export_runtime(args)
    if not args.allow_paid:
        print(json.dumps(plan(args), ensure_ascii=False))
        return 0
    return asyncio.run(generate(args))


if __name__ == "__main__":
    raise SystemExit(main())
