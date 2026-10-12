"use client";
// HX-1 in 3D: turn it, explode it, light it. A port of the enclosure's
// build page (clients/firmware/enclosure/site/index.html) without the build
// sequence. The model and its manifest come from the enclosure's CAD:
//   cd clients/firmware/enclosure && uv run python -m huxcase.site
//   uv run python -m huxcase.site --web <tmp>   -> public/hx1/hx1.glb, landing/hx1/model.json
// Each node's kind picks its material; `explode` is where it sits in the
// exploded view (metres, three.js frame: Y up, front toward +Z).
//
// It never takes the page's scroll: no wheel zoom, and on touch screens a
// vertical swipe scrolls the page (touch-action pan-y) while a sideways one
// turns the model. Animation runs on elapsed time, so it looks the same at
// 60 and 120 Hz, and nothing is drawn while the viewer is off screen.

import { useEffect, useRef, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import model from "./model.json";

type NodeInfo = {
  kind: string;
  label: string;
  explode: number[];
  build_only: boolean;
};
const NODES = model.nodes as Record<string, NodeInfo>;

const VIEWS = {
  assembled: { pos: [0.205, 0.118, 0.305], tgt: [0, 0.05, 0] },
  exploded: { pos: [0.6, 0.26, -0.1], tgt: [0, 0.045, 0.06] }, // parts spread toward the front
} as const;

// A part's name as a translation key: "Speaker, 40 mm 5 W" -> "speaker_40_mm_5_w".
export const partKey = (label: string) =>
  label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_|_$/g, "");

type Api = {
  setExploded: (on: boolean) => void;
  setLight: (on: boolean) => void;
};

const TIP_W = 220; // widest tooltip kept inside the viewer

export function HX1Viewer({ height }: { height: number }) {
  const { t } = useTranslation();
  const mount = useRef<HTMLDivElement>(null);
  const api = useRef<Api | null>(null);
  const [exploded, setExploded] = useState(false);
  const [light, setLight] = useState(false); // the firmware doesn't drive the LEDs yet
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState<"loading" | "ready" | "failed">(
    "loading",
  );
  const [tip, setTip] = useState<{
    x: number;
    y: number;
    label: string;
  } | null>(null);

  useEffect(() => {
    let disposed = false;
    let cleanup = () => {};
    (async () => {
      const THREE = await import("three");
      const { OrbitControls } =
        await import("three/addons/controls/OrbitControls.js");
      const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");
      const { RoomEnvironment } =
        await import("three/addons/environments/RoomEnvironment.js");
      const host = mount.current;
      if (disposed || !host) return;

      const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
      const renderer = new THREE.WebGLRenderer({
        antialias: true,
        alpha: true,
      });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.shadowMap.enabled = true;
      renderer.shadowMap.type = THREE.PCFSoftShadowMap;
      const canvas = renderer.domElement;
      canvas.style.cssText = "display:block;width:100%;height:100%;cursor:grab";
      canvas.setAttribute("aria-hidden", "true");
      host.appendChild(canvas);

      const scene = new THREE.Scene();
      const pmrem = new THREE.PMREMGenerator(renderer);
      const room = new RoomEnvironment();
      const envTarget = pmrem.fromScene(room, 0.04);
      scene.environment = envTarget.texture;
      const camera = new THREE.PerspectiveCamera(26, 1, 0.005, 6);
      const controls = new OrbitControls(camera, canvas);
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;
      controls.maxPolarAngle = Math.PI * 0.53;
      controls.enablePan = false;
      controls.enableZoom = false; // the wheel scrolls the page
      controls.autoRotateSpeed = 0.7;
      canvas.style.touchAction = "pan-y"; // OrbitControls sets none; a vertical swipe scrolls the page

      const key = new THREE.DirectionalLight(0xffffff, 2.4);
      key.position.set(0.12, 0.62, 0.3);
      key.castShadow = true;
      key.shadow.mapSize.set(2048, 2048);
      Object.assign(key.shadow.camera, {
        left: -0.22,
        right: 0.22,
        top: 0.22,
        bottom: -0.22,
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
        new THREE.ShadowMaterial({ opacity: 0.14 }),
      );
      ground.rotation.x = -Math.PI / 2;
      ground.receiveShadow = true;
      scene.add(ground);

      const P = (
        o: ConstructorParameters<typeof THREE.MeshPhysicalMaterial>[0],
      ) => new THREE.MeshPhysicalMaterial(o);
      const MAT: Record<
        string,
        InstanceType<typeof THREE.MeshPhysicalMaterial>
      > = {
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
        accent: P({
          color: 0xff5a14,
          roughness: 0.42,
          clearcoat: 0.35,
          clearcoatRoughness: 0.35,
        }),
        // the face art's second dot colour (huxcase.face_art)
        dot_blue: P({
          color: 0x2f8fe0,
          roughness: 0.42,
          clearcoat: 0.35,
          clearcoatRoughness: 0.35,
        }),
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

      const modelGroup = new THREE.Group();
      scene.add(modelGroup);
      const parts: {
        obj: InstanceType<typeof THREE.Group>;
        to: InstanceType<typeof THREE.Vector3>;
      }[] = [];
      const meshes: InstanceType<typeof THREE.Mesh>[] = [];
      // Warm light inside, behind the board: what will shine through the clear back.
      const inner = new THREE.PointLight(0x6cc8ff, 0, 0.22, 2);
      modelGroup.add(inner);

      let explodeTo = 0,
        explode = 0,
        lightTo = 0,
        lightLevel = 0;
      let camTween: {
        t: number;
        p0: InstanceType<typeof THREE.Vector3>;
        t0: InstanceType<typeof THREE.Vector3>;
        p1: InstanceType<typeof THREE.Vector3>;
        t1: InstanceType<typeof THREE.Vector3>;
      } | null = null;
      let idleAt = performance.now();
      const narrow = () => host.clientWidth < 560;
      const goTo = (name: keyof typeof VIEWS, instant = false) => {
        const v = VIEWS[name],
          k = narrow() ? 1.35 : 1;
        const tgt = new THREE.Vector3(...v.tgt);
        const pos = new THREE.Vector3(...v.pos)
          .sub(tgt)
          .multiplyScalar(k)
          .add(tgt);
        if (instant || reduced) {
          camera.position.copy(pos);
          controls.target.copy(tgt);
          camTween = null;
          return;
        }
        camTween = {
          t: 0,
          p0: camera.position.clone(),
          t0: controls.target.clone(),
          p1: pos,
          t1: tgt,
        };
      };
      goTo("assembled", true);
      const stopSpin = () => {
        controls.autoRotate = false;
        idleAt = performance.now();
      };
      controls.addEventListener("start", () => {
        camTween = null;
        controls.autoRotate = false;
        idleAt = Infinity;
        setTip(null);
      });
      controls.addEventListener("end", () => {
        idleAt = performance.now();
      });

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
          for (const o of found) {
            const info =
              NODES[o.name] || (o.parent ? NODES[o.parent.name] : undefined);
            if (!info || info.build_only) continue;
            o.updateWorldMatrix(true, false);
            o.geometry.applyMatrix4(o.matrixWorld);
            o.position.set(0, 0, 0);
            o.rotation.set(0, 0, 0);
            o.scale.set(1, 1, 1);
            if (!o.geometry.attributes.normal)
              o.geometry.computeVertexNormals();
            o.material = MAT[info.kind] || MAT.shell;
            o.castShadow = true;
            o.receiveShadow = info.kind !== "case_clear";
            o.userData = { label: info.label };
            const g = new THREE.Group();
            g.add(o);
            modelGroup.add(g);
            parts.push({ obj: g, to: new THREE.Vector3(...info.explode) });
            meshes.push(o);
            o.geometry.computeBoundingBox();
            if (o.geometry.boundingBox) box.union(o.geometry.boundingBox);
          }
          if (!meshes.length) {
            setStatus("failed");
            return;
          }
          const c = box.getCenter(new THREE.Vector3());
          modelGroup.position.set(-c.x, -box.min.y, -c.z);
          inner.position.set(...(model.light_at as [number, number, number]));
          api.current = {
            setExploded: (on) => {
              explodeTo = on ? 1 : 0;
              goTo(on ? "exploded" : "assembled");
              stopSpin();
            },
            setLight: (on) => {
              lightTo = on ? 1 : 0;
            },
          };
          setStatus("ready");
        },
        (e) => {
          if (e.total)
            setProgress(Math.min(100, Math.round((100 * e.loaded) / e.total)));
        },
        () => {
          if (!disposed) setStatus("failed");
        },
      );

      // Name the part under the mouse, or under a tap.
      const ray = new THREE.Raycaster(),
        ndc = new THREE.Vector2();
      const nameAt = (clientX: number, clientY: number) => {
        const r = canvas.getBoundingClientRect();
        ndc.set(
          ((clientX - r.left) / r.width) * 2 - 1,
          -((clientY - r.top) / r.height) * 2 + 1,
        );
        ray.setFromCamera(ndc, camera);
        const hit = ray.intersectObjects(meshes, false)[0];
        if (!hit) {
          setTip(null);
          return;
        }
        const x = Math.max(0, Math.min(clientX - r.left + 14, r.width - TIP_W));
        setTip({
          x,
          y: clientY - r.top + 12,
          label: hit.object.userData.label as string,
        });
      };
      let hoverAt = 0;
      const onMove = (e: PointerEvent) => {
        if (e.pointerType !== "mouse" || e.buttons) return;
        const now = performance.now();
        if (now - hoverAt < 40) return;
        hoverAt = now;
        nameAt(e.clientX, e.clientY);
      };
      let down: { x: number; y: number } | null = null;
      const onDown = (e: PointerEvent) => {
        down = { x: e.clientX, y: e.clientY };
      };
      const onUp = (e: PointerEvent) => {
        if (
          e.pointerType !== "mouse" &&
          down &&
          Math.hypot(e.clientX - down.x, e.clientY - down.y) < 6
        ) {
          nameAt(e.clientX, e.clientY);
        }
        down = null;
      };
      const onLeave = () => setTip(null);
      canvas.addEventListener("pointermove", onMove);
      canvas.addEventListener("pointerdown", onDown);
      canvas.addEventListener("pointerup", onUp);
      canvas.addEventListener("pointerleave", onLeave);

      const resize = () => {
        const w = host.clientWidth,
          h = host.clientHeight;
        if (!w || !h) return;
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      };
      const ro = new ResizeObserver(resize);
      ro.observe(host);
      resize();

      // Only draw while on screen.
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
      const ease = (x: number) =>
        x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
      // Exponential approach per second, so the speed doesn't depend on the frame rate.
      const approach = (dt: number, rate: number) => 1 - Math.exp(-rate * dt);
      function frame(now: number) {
        raf = 0;
        if (disposed || !visible) return;
        const dt = Math.min(0.1, (now - last) / 1000);
        last = now;
        if (camTween) {
          camTween.t = Math.min(1, camTween.t + dt / 0.75);
          const k = ease(camTween.t);
          camera.position.lerpVectors(camTween.p0, camTween.p1, k);
          controls.target.lerpVectors(camTween.t0, camTween.t1, k);
          if (camTween.t >= 1) camTween = null;
        }
        if (!reduced && !camTween && now - idleAt > 2500)
          controls.autoRotate = true;
        explode = reduced
          ? explodeTo
          : explode + (explodeTo - explode) * approach(dt, 5);
        const f = ease(Math.min(1, Math.max(0, explode)));
        for (const p of parts) p.obj.position.copy(p.to).multiplyScalar(f);
        lightLevel += (lightTo - lightLevel) * approach(dt, 5);
        const pulse = reduced
          ? 1
          : 0.72 + 0.28 * Math.sin((now / 1000) * ((2 * Math.PI) / 1.9));
        MAT.led.emissiveIntensity = 0.25 + 4.0 * lightLevel * pulse;
        inner.intensity = 0.3 * lightLevel * pulse;
        controls.update(dt);
        renderer.render(scene, camera);
        raf = requestAnimationFrame(frame);
      }
      raf = requestAnimationFrame(frame);

      cleanup = () => {
        cancelAnimationFrame(raf);
        io.disconnect();
        ro.disconnect();
        canvas.removeEventListener("pointermove", onMove);
        canvas.removeEventListener("pointerdown", onDown);
        canvas.removeEventListener("pointerup", onUp);
        canvas.removeEventListener("pointerleave", onLeave);
        controls.dispose();
        for (const m of meshes) m.geometry.dispose();
        for (const m of Object.values(MAT)) m.dispose();
        ground.geometry.dispose();
        (ground.material as InstanceType<typeof THREE.Material>).dispose();
        key.dispose();
        envTarget.dispose();
        room.dispose();
        pmrem.dispose();
        renderer.forceContextLoss();
        renderer.dispose();
        canvas.remove();
        api.current = null;
      };
    })().catch(() => {
      if (!disposed) setStatus("failed");
    });
    return () => {
      disposed = true;
      cleanup();
    };
  }, []);

  const ready = status === "ready";
  const seg: CSSProperties = {
    display: "inline-flex",
    padding: 3,
    gap: 2,
    borderRadius: 999,
    background: "color-mix(in oklab, var(--hux-fg) 8%, transparent)",
  };
  const segBtn = (on: boolean): CSSProperties => ({
    appearance: "none",
    border: "none",
    cursor: ready ? "pointer" : "default",
    padding: "8px 14px",
    borderRadius: 999,
    fontFamily: "var(--hux-sans)",
    fontSize: 13,
    fontWeight: 500,
    color: on ? "var(--hux-paper)" : "var(--hux-fg)",
    background: on ? "var(--hux-fg)" : "transparent",
    opacity: ready ? 1 : 0.5,
  });

  return (
    <div
      style={{
        position: "relative",
        height,
        display: "grid",
        gridTemplateRows: "1fr auto",
        gap: 12,
      }}
    >
      <div
        ref={mount}
        role="img"
        aria-label={t("hx1.viewer.aria")}
        style={{ position: "relative", minHeight: 0, overflow: "hidden" }}
      >
        {!ready && (
          <div
            style={{
              position: "absolute",
              inset: 0,
              display: "grid",
              placeItems: "center",
              fontFamily: "var(--hux-mono)",
              fontSize: 11,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              opacity: 0.6,
              textAlign: "center",
              padding: 24,
            }}
          >
            {status === "failed" ? (
              <span role="alert">{t("hx1.viewer.failed")}</span>
            ) : (
              <span>
                {t("hx1.viewer.loading")} <span aria-hidden>{progress}%</span>
              </span>
            )}
          </div>
        )}
        {tip && (
          <div
            style={{
              position: "absolute",
              left: tip.x,
              top: tip.y,
              maxWidth: TIP_W,
              pointerEvents: "none",
              fontFamily: "var(--hux-mono)",
              fontSize: 11,
              letterSpacing: "0.04em",
              background: "var(--hux-fg)",
              color: "var(--hux-paper)",
              padding: "5px 8px",
              borderRadius: 4,
            }}
          >
            {t(`hx1.partNames.${partKey(tip.label)}`, {
              defaultValue: tip.label,
            })}
          </div>
        )}
      </div>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 10,
        }}
      >
        <div role="group" aria-label={t("hx1.viewer.view")} style={seg}>
          {(["assembled", "exploded"] as const).map((v) => {
            const on = (v === "exploded") === exploded;
            return (
              <button
                key={v}
                type="button"
                aria-pressed={on}
                disabled={!ready}
                style={segBtn(on)}
                onClick={() => {
                  setExploded(v === "exploded");
                  api.current?.setExploded(v === "exploded");
                }}
              >
                {t(`hx1.viewer.${v}`)}
              </button>
            );
          })}
        </div>
        <button
          type="button"
          aria-pressed={light}
          disabled={!ready}
          onClick={() => {
            setLight(!light);
            api.current?.setLight(!light);
          }}
          style={{
            ...segBtn(false),
            border: "1px solid var(--hux-fg-line)",
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <span
            aria-hidden
            style={{
              width: 8,
              height: 8,
              borderRadius: 999,
              background: light ? "var(--hux-coral)" : "transparent",
              border: "1px solid var(--hux-fg-dim)",
            }}
          />
          {t("hx1.viewer.light")}
        </button>
      </div>
    </div>
  );
}
