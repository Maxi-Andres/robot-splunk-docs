"""Render of the Go2 for the motors panel of go2-telemetria-thousandeyes.xml, and where its
12 joints land on it.

Official Unitree model from MuJoCo Menagerie (unitree_go2, BSD-3 by Unitree), standing in its
"home" keyframe; transparent background from the segmentation mask; each joint's anchor is
projected with the same camera, so the panel's dots sit on the real motors.

    python3 -m venv rv && rv/bin/pip install mujoco pillow numpy
    git clone --depth 1 --filter=blob:none --sparse \
        https://github.com/google-deepmind/mujoco_menagerie.git menagerie
    (cd menagerie && git sparse-checkout set unitree_go2)
    MUJOCO_GL=egl rv/bin/python render_go2.py 1.85 140 -16    # distance azimuth elevation

Writes go2_render.png (cropped to the robot) and go2_pts.json (pixel x, y, depth per joint).
The dashboard embeds the render as robot-go2-render.webp. 2026-10-06.
"""
import json, sys, numpy as np, mujoco
from PIL import Image
W, H = 2400, 1800
m = mujoco.MjModel.from_xml_path("menagerie/unitree_go2/go2.xml")
m.vis.headlight.ambient[:] = [0.42, 0.42, 0.44]
m.vis.headlight.diffuse[:] = [0.62, 0.62, 0.62]
m.vis.headlight.specular[:] = [0.25, 0.25, 0.25]
m.vis.global_.fovy = 24.0
d = mujoco.MjData(m)
k = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_KEY, "home")
if k >= 0: mujoco.mj_resetDataKeyframe(m, d, k)
mujoco.mj_forward(m, d)
m.vis.global_.offwidth, m.vis.global_.offheight = W, H
r = mujoco.Renderer(m, H, W)
cam = mujoco.MjvCamera(); cam.type = mujoco.mjtCamera.mjCAMERA_FREE
cam.lookat[:] = d.qpos[:3] + np.array([0.0, 0.0, -0.08])
cam.distance, cam.azimuth, cam.elevation = float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])
r.update_scene(d, camera=cam); rgb = r.render()
r.enable_segmentation_rendering(); r.update_scene(d, camera=cam); seg = r.render(); r.disable_segmentation_rendering()
alpha = (seg[:, :, 0] >= 0).astype(np.uint8) * 255
img = Image.fromarray(np.dstack([rgb, alpha]), "RGBA")
# projection with the scene's GL camera
r.update_scene(d, camera=cam); gc = r.scene.camera[0]
pos = np.array(gc.pos); fwd = np.array(gc.forward); up = np.array(gc.up); right = np.cross(fwd, up)
fb, ft = gc.frustum_bottom, gc.frustum_top; near = gc.frustum_near
half_w = (ft - fb) / 2 * W / H; fl, fr = gc.frustum_center - half_w, gc.frustum_center + half_w
pts = {}
for leg in ("FL", "FR", "RL", "RR"):
    for part in ("hip", "thigh", "calf"):
        j = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, f"{leg}_{part}_joint")
        p = d.xanchor[j] - pos
        z = p @ fwd; x = (p @ right) * near / z; y = (p @ up) * near / z
        u = (x - fl) / (fr - fl) * W; v = (1 - (y - fb) / (ft - fb)) * H
        pts[f"{leg}_{part}"] = [round(float(u), 1), round(float(v), 1), round(float(z), 3)]
bbox = img.getbbox(); img = img.crop(bbox)
pts = {k: [v[0] - bbox[0], v[1] - bbox[1], v[2]] for k, v in pts.items()}
img.save("go2_render.png"); json.dump({"size": img.size, "pts": pts}, open("go2_pts.json", "w"), indent=1)
print(img.size, {k: v[:2] for k, v in pts.items()})
