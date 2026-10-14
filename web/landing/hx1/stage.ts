"use client";
// The 3D stage of the /hx-1 scroll story: one HX-1, posed by the page.
// The page says where the camera is and what the device is doing (exploded,
// lever pressed, how many braille letters are lit); the stage eases there
// on elapsed time, so a jerky scroll still moves smoothly, and draws only
// while it is on screen. The model and its manifest come from the
// enclosure's CAD (huxcase.site --web): public/hx1/hx1.glb, model.json.
//
// Three.js frame: metres, Y up, the device's front toward +Z, its base
// centred on the origin once loaded.

import model from "./model.json";

type NodeInfo = {
  kind: string;
  label: string;
  explode: number[];
  build_only: boolean;
  letter?: string;
};
const NODES = model.nodes as Record<string, NodeInfo>;
const LETTERS = ["h", "o", "l", "a"];

export type V3 = [number, number, number];

export type Pose = {
  pos: V3; // camera position
  tgt: V3; // camera target
  shift: number; // film offset, -1..1 of half the view: >0 puts the model right of centre
  explode: number; // 0 assembled, 1 exploded
  lever: number; // 0 at rest, 1 pressed to the click
  dots: number; // braille letters lit, 0..4 (a fraction fades the next one in)
  spin: number; // 0..1: how much of the slow turntable is on
  fit: number; // 0..1: how much the model is fitted into the band (see Stage.band)
};

export type Stage = {
  pose: (p: Pose, instant?: boolean) => void;
  drag: (on: boolean) => void; // a sideways drag turns the model (the hero only)
  project: (v: V3) => { x: number; y: number } | null; // host pixels, for labels
  anchor: (label: string) => V3 | null; // a part's centre, where it is now
  exploded: () => number; // how far apart the parts are now, 0..1 (eased toward the pose)
  facing: () => number; // how squarely the face points at the camera, -1..1 (0 before it loads)
  // Host pixels, top and bottom, that a pose with fit 1 fills with the whole
  // model, turning or not (the hero: between the page's nav and its words).
  band: (top: number, bottom: number) => void;
  hola: () => V3; // just right of the braille dots, where the "hola." bubble's tail goes
  dispose: () => void;
};

type Opts = {
  onProgress?: (pct: number) => void;
  onReady?: () => void;
  onFail?: () => void;
  onFrame?: () => void;
};

export async function createStage(
  host: HTMLElement,
  opts: Opts,
): Promise<Stage> {
  const THREE = await import("three");
  const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");
  const { RoomEnvironment } =
    await import("three/addons/environments/RoomEnvironment.js");

  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  const canvas = renderer.domElement;
  canvas.style.cssText = "display:block;width:100%;height:100%";
  canvas.setAttribute("aria-hidden", "true");
  host.appendChild(canvas);

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  const room = new RoomEnvironment();
  const envTarget = pmrem.fromScene(room, 0.04);
  scene.environment = envTarget.texture;
  const camera = new THREE.PerspectiveCamera(24, 1, 0.005, 6);

  const key = new THREE.DirectionalLight(0xffffff, 2.4);
  key.position.set(0.12, 0.62, 0.3);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  Object.assign(key.shadow.camera, {
    left: -0.24,
    right: 0.24,
    top: 0.24,
    bottom: -0.24,
    near: 0.05,
    far: 1.5,
  });
  key.shadow.bias = -0.0004;
  key.shadow.normalBias = 0.0015;
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xffffff, 0.9);
  rim.position.set(-0.45, 0.3, -0.5);
  scene.add(rim);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8e8e88, 0.55));
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(3, 3),
    new THREE.ShadowMaterial({ opacity: 0.16 }),
  );
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  scene.add(ground);

  const P = (o: ConstructorParameters<typeof THREE.MeshPhysicalMaterial>[0]) =>
    new THREE.MeshPhysicalMaterial(o);
  const glossy = { roughness: 0.42, clearcoat: 0.35, clearcoatRoughness: 0.35 };
  const MAT: Record<string, InstanceType<typeof THREE.MeshPhysicalMaterial>> = {
    case_white: P({ color: 0xefeee9, roughness: 0.55 }),
    case_red: P({ color: 0xb8291c, roughness: 0.5 }),
    case_clear: P({
      color: 0xf6fafb,
      roughness: 0.32,
      transmission: 0.94,
      thickness: 0.0024,
      ior: 1.57,
      attenuationColor: new THREE.Color(0xe4f0f4),
      attenuationDistance: 0.02,
      side: THREE.DoubleSide,
    }),
    shell: P({ color: 0xbfc0bb, roughness: 0.74 }),
    shell_dark: P({ color: 0x1b1b1b, roughness: 0.62 }),
    shell_inner: P({ color: 0xa8a9a4, roughness: 0.7 }),
    accent: P({ color: 0xff5a14, ...glossy }),
    dot_blue: P({ color: 0x2f8fe0, ...glossy }),
    pcb: P({
      color: 0x173f86,
      roughness: 0.42,
      metalness: 0.05,
      clearcoat: 0.4,
    }),
    led: P({
      color: 0xf4fbff,
      roughness: 0.3,
      emissive: 0x6cc8ff,
      emissiveIntensity: 0.2,
    }),
    metal: P({ color: 0xd2d2d6, metalness: 1, roughness: 0.28 }),
    screw: P({ color: 0xa6a6ac, metalness: 1, roughness: 0.32 }),
    black: P({ color: 0x171717, roughness: 0.5 }),
    white: P({ color: 0xedede8, roughness: 0.45 }),
    speaker: P({ color: 0x2b2b2e, metalness: 0.45, roughness: 0.45 }),
    cone: P({ color: 0x101010, roughness: 0.85 }),
    cell: P({
      color: 0x2f9a6c,
      roughness: 0.3,
      clearcoat: 0.7,
      clearcoatRoughness: 0.2,
    }),
    wire_black: P({ color: 0x151515, roughness: 0.35, clearcoat: 0.5 }),
    wire_red: P({ color: 0xbf1720, roughness: 0.35, clearcoat: 0.5 }),
    wire_white: P({ color: 0xe9e7df, roughness: 0.45 }),
  };
  // Each braille letter gets its own material, so it can light up on its own.
  const letterMat: Record<
    string,
    InstanceType<typeof THREE.MeshPhysicalMaterial>
  > = {};
  const letterColor: Record<string, InstanceType<typeof THREE.Color>> = {};
  const dimOf: Record<string, InstanceType<typeof THREE.Color>> = {};

  const turntable = new THREE.Group(); // spins about the device's own centre
  scene.add(turntable);
  const modelGroup = new THREE.Group();
  turntable.add(modelGroup);
  type Part = {
    obj: InstanceType<typeof THREE.Group>;
    to: InstanceType<typeof THREE.Vector3>;
    label: string;
    mesh: InstanceType<typeof THREE.Mesh>;
  };
  const parts: Part[] = [];
  let leverPivot: InstanceType<typeof THREE.Group> | null = null;
  let spring: InstanceType<typeof THREE.Group> | null = null;

  // The pose: where the page wants it (want) and where it is (now).
  const now: Pose = {
    pos: [0.14, 0.1, 0.36],
    tgt: [0, 0.052, 0],
    shift: 0,
    explode: 0,
    lever: 0,
    dots: 0,
    spin: 0,
    fit: 0,
  };
  let want: Pose = { ...now };
  // The model's bounding cylinder about its turning axis (set on load), and
  // the band a fitted pose fills.
  let ext: { r: number; h: number } | null = null;
  let fitBand: [number, number] | null = null;
  let dragOn = false;
  let yaw = 0, // turntable angle
    yawVel = 0;

  let disposed = false;
  const loaded = new Promise<void>((resolve) => {
    new GLTFLoader().load(
      "/hx1/hx1.glb",
      (gltf) => {
        if (disposed) return;
        const box = new THREE.Box3();
        const found: InstanceType<typeof THREE.Mesh>[] = [];
        gltf.scene.traverse((o) => {
          if ((o as InstanceType<typeof THREE.Mesh>).isMesh)
            found.push(o as InstanceType<typeof THREE.Mesh>);
        });
        const lever = model.lever as {
          pivot: V3;
          press_deg: number;
          spring: { inner: V3; length: number; arm: number };
        };
        for (const o of found) {
          const info =
            NODES[o.name] || (o.parent ? NODES[o.parent.name] : undefined);
          if (!info || info.build_only) continue;
          o.updateWorldMatrix(true, false);
          o.geometry.applyMatrix4(o.matrixWorld);
          o.position.set(0, 0, 0);
          o.rotation.set(0, 0, 0);
          o.scale.set(1, 1, 1);
          if (!o.geometry.attributes.normal) o.geometry.computeVertexNormals();
          if (info.letter) {
            const m = (MAT[info.kind] || MAT.shell).clone();
            letterMat[info.letter] = m;
            letterColor[info.letter] = m.color.clone();
            dimOf[info.letter] = m.color.clone().lerp(new THREE.Color(0xd8d6d0), 0.72);
            o.material = m;
          } else {
            o.material = MAT[info.kind] || MAT.shell;
          }
          o.castShadow = true;
          o.receiveShadow = info.kind !== "case_clear";
          const g = new THREE.Group();
          if (info.label === "Spring 0.5 × 5 × 15") {
            // Fixed at its inner end on the plunger's flange; it shortens
            // along X as the lever comes in.
            const sp = lever.spring;
            const hold = new THREE.Group();
            hold.position.set(...sp.inner);
            o.position.set(-sp.inner[0], -sp.inner[1], -sp.inner[2]);
            hold.add(o);
            g.add(hold);
            spring = hold;
          } else if (info.label === "Talk lever") {
            // The lever turns on its pin: a pivot group at the pin, the mesh
            // offset back, so rotating the group swings the lever.
            const pivot = new THREE.Group();
            pivot.position.set(...lever.pivot);
            o.position.set(-lever.pivot[0], -lever.pivot[1], -lever.pivot[2]);
            pivot.add(o);
            g.add(pivot);
            leverPivot = pivot;
          } else {
            g.add(o);
          }
          modelGroup.add(g);
          parts.push({
            obj: g,
            to: new THREE.Vector3(...info.explode),
            label: info.label,
            mesh: o,
          });
          o.geometry.computeBoundingBox();
          if (o.geometry.boundingBox) box.union(o.geometry.boundingBox);
        }
        if (!parts.length) {
          opts.onFail?.();
          return;
        }
        const c = box.getCenter(new THREE.Vector3());
        modelGroup.position.set(-c.x, -box.min.y, -c.z);
        ext = {
          r: Math.hypot((box.max.x - box.min.x) / 2, (box.max.z - box.min.z) / 2),
          h: box.max.y - box.min.y,
        };
        dirty = true;
        opts.onReady?.();
        resolve();
      },
      (e) => {
        if (e.total)
          opts.onProgress?.(
            Math.min(100, Math.round((100 * e.loaded) / e.total)),
          );
      },
      () => {
        if (!disposed) opts.onFail?.();
      },
    );
  });
  void loaded;

  // A sideways drag turns the model; a vertical one is left to the page.
  let dragX: number | null = null;
  const onDown = (e: PointerEvent) => {
    if (!dragOn) return;
    dragX = e.clientX;
    canvas.setPointerCapture(e.pointerId);
  };
  const onMove = (e: PointerEvent) => {
    if (dragX === null) return;
    const dx = e.clientX - dragX;
    dragX = e.clientX;
    yaw += dx * 0.012;
    yawVel = dx * 0.012 * 60;
  };
  const onUp = () => {
    dragX = null;
  };
  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("pointercancel", onUp);

  let W = 1,
    H = 1,
    dirty = true; // draw the next frame even if nothing moved
  const resize = () => {
    const w = host.clientWidth,
      h = host.clientHeight;
    if (!w || !h) return;
    W = w;
    H = h;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    dirty = true;
  };
  const ro = new ResizeObserver(resize);
  ro.observe(host);
  resize();

  const v = new THREE.Vector3();
  const fitV = new THREE.Vector3();
  const fitM = new THREE.Matrix4();
  const tgtV = new THREE.Vector3();
  const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
  const approach = (dt: number, rate: number) =>
    reduced ? 1 : 1 - Math.exp(-rate * dt);

  let drawn: number[] = [];
  let visible = true,
    raf = 0,
    last = performance.now();
  const io = new IntersectionObserver(([en]) => {
    visible = en.isIntersecting;
    if (visible && !raf) {
      last = performance.now();
      raf = requestAnimationFrame(frame);
    }
  });
  io.observe(host);

  function frame(t: number) {
    raf = 0;
    if (disposed || !visible) return;
    const dt = Math.min(0.1, (t - last) / 1000);
    last = t;
    const k = approach(dt, 5);
    for (let i = 0; i < 3; i++) {
      now.pos[i] = lerp(now.pos[i], want.pos[i], k);
      now.tgt[i] = lerp(now.tgt[i], want.tgt[i], k);
    }
    now.shift = lerp(now.shift, want.shift, k);
    now.explode = lerp(now.explode, want.explode, k);
    now.lever = lerp(now.lever, want.lever, approach(dt, 9));
    now.dots = lerp(now.dots, want.dots, approach(dt, 7));
    now.spin = lerp(now.spin, want.spin, k);
    now.fit = lerp(now.fit, want.fit, k);

    // The turntable: a slow turn while spin is on, and a drag's coast;
    // with spin off it comes home to face the camera the short way round.
    if (dragX === null) {
      yaw += yawVel * dt;
      yawVel *= Math.exp(-3 * dt);
      if (!reduced) yaw += 0.22 * now.spin * dt;
      if (want.spin < 0.5 && Math.abs(yawVel) < 0.05) {
        const home = Math.round(yaw / (2 * Math.PI)) * 2 * Math.PI;
        yaw = lerp(yaw, home, approach(dt, 3));
      }
    }
    turntable.rotation.y = yaw;

    camera.position.set(...now.pos);
    tgtV.set(...now.tgt);
    camera.lookAt(tgtV);
    // The shift as a share of the view's half-width (0.38 at 0.42), the same
    // on a portrait screen as on a wide one; three.js scales a film offset
    // by 1 / (aspect * tan(fov / 2)).
    camera.filmOffset = -now.shift * camera.getFilmWidth() * 0.31 * (camera.aspect / 1.6);
    camera.updateProjectionMatrix();
    // Fitted: scale and move the picture (not the camera) so the model's
    // bounding cylinder spans the band exactly, at any window size.
    if (now.fit > 0.001 && ext && fitBand) {
      camera.updateMatrixWorld();
      let lo = Infinity,
        hi = -Infinity;
      for (const sx of [-1, 1])
        for (const sz of [-1, 1])
          for (const y of [0, ext.h]) {
            fitV.set(sx * ext.r, y, sz * ext.r).project(camera);
            lo = Math.min(lo, fitV.y);
            hi = Math.max(hi, fitV.y);
          }
      const top = 1 - (2 * fitBand[0]) / H,
        bottom = 1 - (2 * fitBand[1]) / H;
      const sc = Math.min(1.3, Math.max(0.4, (top - bottom) / Math.max(1e-6, hi - lo)));
      const S = lerp(1, sc, now.fit),
        T = lerp(0, top - sc * hi, now.fit);
      fitM.set(S, 0, 0, 0, 0, S, 0, T, 0, 0, 1, 0, 0, 0, 0, 1);
      camera.projectionMatrix.premultiply(fitM);
      camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
    }

    const ease = (x: number) =>
      x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
    const f = ease(Math.min(1, Math.max(0, now.explode)));
    for (const p of parts) p.obj.position.copy(p.to).multiplyScalar(f);
    if (leverPivot) {
      const lv = model.lever as {
        press_deg: number;
        spring: { length: number; arm: number };
      };
      const a = (now.lever * lv.press_deg * Math.PI) / 180;
      leverPivot.rotation.z = a;
      // The lever's top end comes in by arm * sin(a): the spring takes it up.
      if (spring) spring.scale.x = 1 - (lv.spring.arm * Math.sin(a)) / lv.spring.length;
    }
    LETTERS.forEach((L, i) => {
      const m = letterMat[L];
      const full = letterColor[L];
      if (!m || !full) return;
      // Unlit letters sit dim; each one comes up to its colour with a glow.
      const lit = Math.min(1, Math.max(0, now.dots - i));
      m.color.copy(dimOf[L]).lerp(full, lit);
      m.emissive.copy(full);
      m.emissiveIntensity = 0.35 * lit;
    });

    // Draw only when something moved: a settled pose costs nothing.
    const state = [...now.pos, ...now.tgt, now.shift, f, now.lever, now.dots, now.fit, yaw, W, H];
    let moved = dirty;
    for (let i = 0; i < state.length && !moved; i++)
      moved = Math.abs(state[i] - drawn[i]) > 1e-7;
    if (moved) {
      renderer.render(scene, camera);
      drawn = state;
      dirty = false;
    }
    opts.onFrame?.();
    raf = requestAnimationFrame(frame);
  }
  raf = requestAnimationFrame(frame);

  const toScreen = (world: InstanceType<typeof THREE.Vector3>) => {
    world.project(camera);
    if (world.z > 1) return null;
    return { x: ((world.x + 1) / 2) * W, y: ((1 - world.y) / 2) * H };
  };

  return {
    pose(p, instant) {
      want = { ...p, pos: [...p.pos], tgt: [...p.tgt] };
      if (instant || reduced) {
        Object.assign(now, { ...want, pos: [...want.pos], tgt: [...want.tgt] });
      }
    },
    drag(on) {
      dragOn = on;
      canvas.style.cursor = on ? "grab" : "default";
      canvas.style.touchAction = on ? "pan-y" : "auto";
      canvas.style.pointerEvents = on ? "auto" : "none";
    },
    project(p) {
      return toScreen(v.set(...p));
    },
    anchor(label) {
      const hit = parts.filter((p) => p.label === label);
      if (!hit.length) return null;
      const b = new THREE.Box3();
      for (const p of hit) b.expandByObject(p.mesh);
      const c = b.getCenter(new THREE.Vector3());
      return [c.x, c.y, c.z];
    },
    exploded() {
      return now.explode;
    },
    band(top, bottom) {
      if (fitBand && fitBand[0] === top && fitBand[1] === bottom) return;
      fitBand = [top, bottom];
      dirty = true;
    },
    facing() {
      if (!parts.length) return 0;
      // the face's normal (+Z, turned by the turntable) against the way to the camera
      const dx = now.pos[0] - now.tgt[0],
        dz = now.pos[2] - now.tgt[2];
      return (Math.sin(yaw) * dx + Math.cos(yaw) * dz) / Math.max(1e-6, Math.hypot(dx, dz));
    },
    hola() {
      const at = model.hola_at as V3;
      const p = new THREE.Vector3(at[0] + 0.04, at[1] + 0.016, at[2]);
      modelGroup.localToWorld(p);
      return [p.x, p.y, p.z];
    },
    dispose() {
      disposed = true;
      cancelAnimationFrame(raf);
      io.disconnect();
      ro.disconnect();
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("pointercancel", onUp);
      for (const p of parts) p.mesh.geometry.dispose();
      for (const m of [...Object.values(MAT), ...Object.values(letterMat)])
        m.dispose();
      ground.geometry.dispose();
      (ground.material as InstanceType<typeof THREE.Material>).dispose();
      key.dispose();
      envTarget.dispose();
      room.dispose();
      pmrem.dispose();
      renderer.forceContextLoss();
      renderer.dispose();
      canvas.remove();
    },
  };
}
