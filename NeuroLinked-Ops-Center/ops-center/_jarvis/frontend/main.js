// J.A.R.V.I.S. — 3D Neurolink Brain + Dual-Voice Audio-Reactive Speech UI
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { FXAAShader } from 'three/addons/shaders/FXAAShader.js';

// ========================================================================
//   STATE MANAGEMENT
// ========================================================================
const STATE = { IDLE: 'idle', LISTENING: 'listening', THINKING: 'thinking', SPEAKING: 'speaking', BRAIN: 'brain' };
let currentState = STATE.IDLE;
let micLevel = 0;    // 0-1, smoothed mic volume
let ttsLevel = 0;    // 0-1, smoothed TTS playback volume
let reactiveLevel = 0; // whichever of mic/tts is active
let glitchLevel = 0;  // 0-1, chromatic aberration / tear intensity, decays over time

const $ = (id) => document.getElementById(id);
const stateLabel = $('state-label');
const hintText = $('hint-text');
const micStatus = $('mic-status');
const micBar = $('mic-bar');
const liveTranscript = $('live-transcript');
const transcriptBox = $('transcript');

function setState(s) {
    if (s !== currentState) glitchLevel = Math.max(glitchLevel, 0.85); // spike glitch on state change
    currentState = s;
    document.body.className = `state-${s}`;
    stateLabel.textContent = s.toUpperCase();
}

// ========================================================================
//   AUDIO ANALYSIS (mic + TTS)
// ========================================================================
let audioCtx = null;
let micAnalyser = null;
let micData = null;
let ttsAnalyser = null;
let ttsData = null;

async function initMicAnalyser() {
    if (micAnalyser) return true;
    try {
        if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const stream = await navigator.mediaDevices.getUserMedia({
            audio: {
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
            }
        });
        const source = audioCtx.createMediaStreamSource(stream);
        micAnalyser = audioCtx.createAnalyser();
        micAnalyser.fftSize = 256;
        micAnalyser.smoothingTimeConstant = 0.6;
        source.connect(micAnalyser);
        micData = new Uint8Array(micAnalyser.frequencyBinCount);
        micStatus.textContent = 'MIC · ACTIVE';

        return true;
    } catch (err) {
        console.error('[habibi] Mic init failed:', err);
        micStatus.textContent = 'MIC · DENIED';
        return false;
    }
}


function sampleMic() {
    if (!micAnalyser) return 0;
    micAnalyser.getByteFrequencyData(micData);
    let sum = 0;
    for (let i = 0; i < micData.length; i++) sum += micData[i];
    const avg = sum / micData.length / 255;
    return Math.min(1, avg * 2.5); // amplify quiet voices
}

function attachTtsAnalyser(audioEl) {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    try {
        const src = audioCtx.createMediaElementSource(audioEl);
        ttsAnalyser = audioCtx.createAnalyser();
        ttsAnalyser.fftSize = 256;
        ttsAnalyser.smoothingTimeConstant = 0.7;
        src.connect(ttsAnalyser);
        ttsAnalyser.connect(audioCtx.destination);
        ttsData = new Uint8Array(ttsAnalyser.frequencyBinCount);
    } catch (err) {
        // already connected - fine
    }
}

function sampleTts() {
    if (!ttsAnalyser) return 0;
    ttsAnalyser.getByteFrequencyData(ttsData);
    let sum = 0;
    for (let i = 0; i < ttsData.length; i++) sum += ttsData[i];
    return Math.min(1, (sum / ttsData.length / 255) * 1.8);
}

// ========================================================================
//   THREE.JS SCENE — the Arc Reactor
// ========================================================================
const canvas = $('scene');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setClearColor(0x000000, 1);

const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x000814, 0.05);

const camera = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 0.1, 100);
camera.position.set(0, 0, 6);

// --- Core: deforming icosahedron with custom shader (brain-like) ---
const coreGeometry = new THREE.IcosahedronGeometry(0.95, 7);

const coreUniforms = {
    uTime: { value: 0 },
    uReactive: { value: 0 },
    uColorA: { value: new THREE.Color(0x58e1ff) },
    uColorB: { value: new THREE.Color(0x8ef0ff) },
    uEmissive: { value: 1.0 },
};

const coreMaterial = new THREE.ShaderMaterial({
    uniforms: coreUniforms,
    vertexShader: `
        uniform float uTime;
        uniform float uReactive;
        varying vec3 vNormal;
        varying float vDisplace;

        // 3D simplex noise (Ashima)
        vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
        vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
        vec4 permute(vec4 x) { return mod289(((x*34.0)+1.0)*x); }
        vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

        float snoise(vec3 v) {
            const vec2 C = vec2(1.0/6.0, 1.0/3.0);
            const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
            vec3 i  = floor(v + dot(v, C.yyy));
            vec3 x0 = v - i + dot(i, C.xxx);
            vec3 g = step(x0.yzx, x0.xyz);
            vec3 l = 1.0 - g;
            vec3 i1 = min(g.xyz, l.zxy);
            vec3 i2 = max(g.xyz, l.zxy);
            vec3 x1 = x0 - i1 + C.xxx;
            vec3 x2 = x0 - i2 + C.yyy;
            vec3 x3 = x0 - D.yyy;
            i = mod289(i);
            vec4 p = permute(permute(permute(
                     i.z + vec4(0.0, i1.z, i2.z, 1.0))
                   + i.y + vec4(0.0, i1.y, i2.y, 1.0))
                   + i.x + vec4(0.0, i1.x, i2.x, 1.0));
            float n_ = 0.142857142857;
            vec3 ns = n_ * D.wyz - D.xzx;
            vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
            vec4 x_ = floor(j * ns.z);
            vec4 y_ = floor(j - 7.0 * x_);
            vec4 x = x_ *ns.x + ns.yyyy;
            vec4 y = y_ *ns.x + ns.yyyy;
            vec4 h = 1.0 - abs(x) - abs(y);
            vec4 b0 = vec4(x.xy, y.xy);
            vec4 b1 = vec4(x.zw, y.zw);
            vec4 s0 = floor(b0)*2.0 + 1.0;
            vec4 s1 = floor(b1)*2.0 + 1.0;
            vec4 sh = -step(h, vec4(0.0));
            vec4 a0 = b0.xzyw + s0.xzyw*sh.xxyy;
            vec4 a1 = b1.xzyw + s1.xzyw*sh.zzww;
            vec3 p0 = vec3(a0.xy, h.x);
            vec3 p1 = vec3(a0.zw, h.y);
            vec3 p2 = vec3(a1.xy, h.z);
            vec3 p3 = vec3(a1.zw, h.w);
            vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2,p2), dot(p3,p3)));
            p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
            vec4 m = max(0.6 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
            m = m * m;
            return 42.0 * dot(m*m, vec4(dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3)));
        }

        void main() {
            vNormal = normal;
            float t = uTime * 0.4;
            // multiple octaves for brain-like convoluted surface
            float n1 = snoise(normal * 2.2 + vec3(t, t * 0.7, t * 1.3)) * 0.18;
            float n2 = snoise(normal * 5.5 + vec3(t * 1.5)) * 0.09;
            float n3 = snoise(normal * 11.0 + vec3(t * 2.5)) * 0.04;
            float pulse = snoise(normal * 3.0 + vec3(t * 2.0)) * 0.06;
            float displace = n1 + n2 + n3 + pulse + uReactive * 0.45 * (0.5 + 0.5 * snoise(normal * 4.0 + t * 3.0));
            vDisplace = displace;
            vec3 pos = position + normal * displace;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(pos, 1.0);
        }
    `,
    fragmentShader: `
        uniform vec3 uColorA;
        uniform vec3 uColorB;
        uniform float uEmissive;
        uniform float uTime;
        uniform float uReactive;
        varying vec3 vNormal;
        varying float vDisplace;

        void main() {
            float fres = pow(1.0 - abs(dot(normalize(vNormal), vec3(0.0, 0.0, 1.0))), 2.8);
            vec3 base = mix(uColorA, uColorB, fres);
            float energy = 0.25 + 0.45 * abs(vDisplace * 4.0);
            vec3 col = base * energy * uEmissive;
            col += vec3(0.7) * pow(fres, 6.0) * (0.3 + uReactive * 0.5);
            gl_FragColor = vec4(col, 0.92);
        }
    `,
    transparent: true,
});
const core = new THREE.Mesh(coreGeometry, coreMaterial);
scene.add(core);

// --- 4D Tesseract (hypercube) projected into 3D, at the center of the core ---
// 16 vertices in 4D: all combinations of (±1, ±1, ±1, ±1)
const TESSERACT_VERTS_4D = [];
for (let x = -1; x <= 1; x += 2)
    for (let y = -1; y <= 1; y += 2)
        for (let z = -1; z <= 1; z += 2)
            for (let w = -1; w <= 1; w += 2)
                TESSERACT_VERTS_4D.push([x, y, z, w]);
// Edges: two vertices connected iff they differ in exactly one coordinate → 32 edges
const TESSERACT_EDGES = [];
for (let i = 0; i < 16; i++) {
    for (let j = i + 1; j < 16; j++) {
        let diff = 0;
        for (let k = 0; k < 4; k++) if (TESSERACT_VERTS_4D[i][k] !== TESSERACT_VERTS_4D[j][k]) diff++;
        if (diff === 1) TESSERACT_EDGES.push([i, j]);
    }
}
const tesseractGeo = new THREE.BufferGeometry();
const tesseractPos = new Float32Array(TESSERACT_EDGES.length * 2 * 3);
tesseractGeo.setAttribute('position', new THREE.BufferAttribute(tesseractPos, 3));
const tesseractMat = new THREE.ShaderMaterial({
    uniforms: {
        uColor: { value: new THREE.Color(0xffffff) },
        uReactive: { value: 0 },
        uTime: { value: 0 },
    },
    vertexShader: `
        varying vec3 vPos;
        void main() {
            vPos = position;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
    `,
    fragmentShader: `
        uniform vec3 uColor;
        uniform float uReactive;
        uniform float uTime;
        varying vec3 vPos;
        void main() {
            float pulse = 0.6 + 0.4 * sin(uTime * 3.0 + length(vPos) * 4.0);
            gl_FragColor = vec4(uColor, pulse * (0.55 + uReactive * 0.45));
        }
    `,
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
});
const tesseract = new THREE.LineSegments(tesseractGeo, tesseractMat);
tesseract.scale.setScalar(0.55);
scene.add(tesseract);

// Update tesseract vertices each frame by rotating in 4D then projecting to 3D
function updateTesseract(t, reactive) {
    const aXW = t * 0.6 + reactive * 0.8;
    const aYW = t * 0.45;
    const aZW = t * 0.35 + reactive * 0.5;
    const aXY = t * 0.2;
    const cXW = Math.cos(aXW), sXW = Math.sin(aXW);
    const cYW = Math.cos(aYW), sYW = Math.sin(aYW);
    const cZW = Math.cos(aZW), sZW = Math.sin(aZW);
    const cXY = Math.cos(aXY), sXY = Math.sin(aXY);
    const D = 2.5; // 4D viewing distance
    const projected = TESSERACT_VERTS_4D.map(v => {
        let [x, y, z, w] = v;
        // rotate in XW
        [x, w] = [x * cXW - w * sXW, x * sXW + w * cXW];
        // rotate in YW
        [y, w] = [y * cYW - w * sYW, y * sYW + w * cYW];
        // rotate in ZW
        [z, w] = [z * cZW - w * sZW, z * sZW + w * cZW];
        // rotate in XY (adds tumble)
        [x, y] = [x * cXY - y * sXY, x * sXY + y * cXY];
        // project 4D → 3D via perspective division
        const s = 1 / (D - w);
        return [x * s, y * s, z * s];
    });
    const arr = tesseractGeo.attributes.position.array;
    for (let i = 0; i < TESSERACT_EDGES.length; i++) {
        const [a, b] = TESSERACT_EDGES[i];
        const pa = projected[a], pb = projected[b];
        arr[i * 6] = pa[0]; arr[i * 6 + 1] = pa[1]; arr[i * 6 + 2] = pa[2];
        arr[i * 6 + 3] = pb[0]; arr[i * 6 + 4] = pb[1]; arr[i * 6 + 5] = pb[2];
    }
    tesseractGeo.attributes.position.needsUpdate = true;
}

// --- Soft wireframe inner shell (skull / cortex hint) ---
const wireGeo = new THREE.IcosahedronGeometry(1.35, 3);
const wireMat = new THREE.MeshBasicMaterial({
    color: 0x6bb8d0,
    wireframe: true,
    transparent: true,
    opacity: 0.08,
});
const wireShell = new THREE.Mesh(wireGeo, wireMat);
scene.add(wireShell);

// --- Neural BRAIN REGIONS: clustered neurons, each colored by function ---
// Each region maps to a thought-kind. When Jarvis fires a matching tool,
// the region's firing rate jumps, its neurons brighten, and thought orbs
// spawn from that region's cluster (instead of the uniform core).
// Color palette is aligned with the NeuroLinked Brain's REGION_COLORS
// (dashboard/js/brain3d.js) so the two views light up with the SAME hue when
// the SAME cortex fires. `brain_id` is the region key the Brain uses — emitted
// via postMessage so the Brain iframe can excite the matching region in sync.
const BRAIN_REGIONS = [
    // id,           brain_id,        label,              kind,        color,     center (x,y,z),       count
    { id: "prefrontal",  brain_id: "prefrontal",     label: "PREFRONTAL",    kind: "directive", color: new THREE.Color(0xaa88ff), pos: new THREE.Vector3(-1.3, 1.9, 0.5),  count: 140 },
    { id: "motor",       brain_id: "motor_cortex",   label: "MOTOR CORTEX",  kind: "control",   color: new THREE.Color(0xff3366), pos: new THREE.Vector3( 1.6, 1.6, 0.3),  count: 190 },
    { id: "concept",     brain_id: "concept_layer",  label: "CONCEPT LAYER", kind: "task",      color: new THREE.Color(0xffcc00), pos: new THREE.Vector3(-2.0, 0.6,-0.7),  count: 160 },
    { id: "association", brain_id: "association",    label: "ASSOCIATION",   kind: "brain",     color: new THREE.Color(0xff6600), pos: new THREE.Vector3(-0.2, 0.7, 0.4),  count: 260 },
    { id: "sensory",     brain_id: "sensory_cortex", label: "SENSORY CORTEX",kind: "vision",    color: new THREE.Color(0x00ffff), pos: new THREE.Vector3( 2.2, 0.2,-0.2),  count: 200 },
    { id: "predictive",  brain_id: "predictive",     label: "PREDICTIVE",    kind: "memory",    color: new THREE.Color(0xff00ff), pos: new THREE.Vector3(-2.4,-0.4, 0.5),  count: 180 },
    { id: "feature",     brain_id: "feature_layer",  label: "FEATURE LAYER", kind: "code",      color: new THREE.Color(0x00ccff), pos: new THREE.Vector3( 0.9,-0.9,-0.4),  count: 180 },
    { id: "brainstem",   brain_id: "brainstem",      label: "BRAINSTEM",     kind: "shell",     color: new THREE.Color(0xff8800), pos: new THREE.Vector3( 0.0,-2.0, 0.4),  count: 120 },
    { id: "hippocampus", brain_id: "hippocampus",    label: "HIPPOCAMPUS",   kind: "note",      color: new THREE.Color(0x00ff88), pos: new THREE.Vector3(-1.3,-1.6, 0.3),  count: 160 },
    { id: "language",    brain_id: "reflex_arc",     label: "LANGUAGE",      kind: "input",     color: new THREE.Color(0xff4444), pos: new THREE.Vector3( 1.9,-1.3,-0.1),  count: 170 },
];
// Runtime state per region (mutated as Jarvis operates)
const regionState = {};
BRAIN_REGIONS.forEach(r => {
    regionState[r.id] = { firing: 0.008 + Math.random() * 0.008, lastSpike: 0 };
});
// kind → region.id lookup (for fast routing from thought events)
const KIND_TO_REGION = {};
BRAIN_REGIONS.forEach(r => { KIND_TO_REGION[r.kind] = r.id; });
// Fallback for kinds not mapped above
const FALLBACK_REGION_ID = "association";

// Build neuron data: each neuron belongs to a region, scattered around its center
const nodePositions = [];      // Vector3[] — also used by dendrite spawn points
const nodeRegionIdx = [];      // int[] — index into BRAIN_REGIONS for each neuron
const nodeRegionId  = [];      // string[] same length
const regionNodeIndices = {};  // region.id → [neuron indices]
BRAIN_REGIONS.forEach(r => { regionNodeIndices[r.id] = []; });

// Gaussian jitter helper
function randn() {
    let u = 0, v = 0;
    while (u === 0) u = Math.random();
    while (v === 0) v = Math.random();
    return Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v);
}

BRAIN_REGIONS.forEach((region, ri) => {
    const sigma = 0.45; // spread of neurons within a region
    for (let i = 0; i < region.count; i++) {
        const p = region.pos.clone().add(new THREE.Vector3(
            randn() * sigma,
            randn() * sigma,
            randn() * sigma * 0.7,
        ));
        const idx = nodePositions.length;
        nodePositions.push(p);
        nodeRegionIdx.push(ri);
        nodeRegionId.push(region.id);
        regionNodeIndices[region.id].push(idx);
    }
});
const NEURAL_NODES = nodePositions.length;  // total count (≈ 1760)

// Connections: each neuron connects to ~3 nearest neighbors (mostly within region,
// since intra-region neurons are closer). This produces visible cluster structure
// with occasional bridges between adjacent regions.
const linePositionsArr = [];
const lineColorsArr    = [];
const lineAlphasArr    = [];
for (let i = 0; i < NEURAL_NODES; i++) {
    const pi = nodePositions[i];
    const dists = [];
    // For perf, restrict to candidates within ~1.2 units
    for (let j = 0; j < NEURAL_NODES; j++) {
        if (i === j) continue;
        const d2 = pi.distanceToSquared(nodePositions[j]);
        if (d2 < 1.44) dists.push({ j, d: d2 });
    }
    dists.sort((a, b) => a.d - b.d);
    const connectCount = Math.min(3, dists.length);
    const regColor = BRAIN_REGIONS[nodeRegionIdx[i]].color;
    for (let k = 0; k < connectCount; k++) {
        const a = nodePositions[i];
        const b = nodePositions[dists[k].j];
        linePositionsArr.push(a.x, a.y, a.z, b.x, b.y, b.z);
        // Both endpoints share the source neuron's region color
        lineColorsArr.push(regColor.r, regColor.g, regColor.b);
        lineColorsArr.push(regColor.r, regColor.g, regColor.b);
        const alpha = 0.22 + Math.random() * 0.35;
        lineAlphasArr.push(alpha, alpha);
    }
}

const meshGeo = new THREE.BufferGeometry();
meshGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(linePositionsArr), 3));
meshGeo.setAttribute('color',    new THREE.BufferAttribute(new Float32Array(lineColorsArr),    3));
meshGeo.setAttribute('aAlpha',   new THREE.BufferAttribute(new Float32Array(lineAlphasArr),    1));

const meshMat = new THREE.ShaderMaterial({
    uniforms: {
        uTime:     { value: 0 },
        uReactive: { value: 0 },
    },
    vertexShader: `
        attribute float aAlpha;
        attribute vec3 color;
        varying float vAlpha;
        varying vec3 vColor;
        void main() {
            vAlpha = aAlpha;
            vColor = color;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
    `,
    fragmentShader: `
        uniform float uTime;
        uniform float uReactive;
        varying float vAlpha;
        varying vec3 vColor;
        void main() {
            float shimmer = 0.5 + 0.5 * sin(uTime * 3.0 + vAlpha * 40.0);
            float a = vAlpha * (0.22 + shimmer * 0.3 + uReactive * 0.35);
            gl_FragColor = vec4(vColor, a);
        }
    `,
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
});
const neuralMesh = new THREE.LineSegments(meshGeo, meshMat);
scene.add(neuralMesh);

// Glowing node dots — colored per region, size/brightness driven by region firing rate
const nodeGeo = new THREE.BufferGeometry();
const nodeArr        = new Float32Array(NEURAL_NODES * 3);
const nodeColorArr   = new Float32Array(NEURAL_NODES * 3);
const nodeSizes      = new Float32Array(NEURAL_NODES);
const nodeRegionArr  = new Float32Array(NEURAL_NODES);  // region index as float
nodePositions.forEach((p, i) => {
    nodeArr[i * 3]     = p.x;
    nodeArr[i * 3 + 1] = p.y;
    nodeArr[i * 3 + 2] = p.z;
    const c = BRAIN_REGIONS[nodeRegionIdx[i]].color;
    nodeColorArr[i * 3]     = c.r;
    nodeColorArr[i * 3 + 1] = c.g;
    nodeColorArr[i * 3 + 2] = c.b;
    nodeSizes[i]       = 0.035 + Math.random() * 0.04;
    nodeRegionArr[i]   = nodeRegionIdx[i];
});
nodeGeo.setAttribute('position', new THREE.BufferAttribute(nodeArr, 3));
nodeGeo.setAttribute('color',    new THREE.BufferAttribute(nodeColorArr, 3));
nodeGeo.setAttribute('size',     new THREE.BufferAttribute(nodeSizes, 1));
nodeGeo.setAttribute('aRegion',  new THREE.BufferAttribute(nodeRegionArr, 1));

// Firing rate per region passed in as a uniform array, indexed by aRegion
const REGION_COUNT = BRAIN_REGIONS.length;
const regionFiringUniform = new Float32Array(REGION_COUNT);

const nodeMat = new THREE.ShaderMaterial({
    uniforms: {
        uTime:     { value: 0 },
        uReactive: { value: 0 },
        uFiring:   { value: regionFiringUniform },
    },
    vertexShader: `
        attribute float size;
        attribute vec3 color;
        attribute float aRegion;
        uniform float uTime;
        uniform float uReactive;
        uniform float uFiring[${REGION_COUNT}];
        varying float vFlicker;
        varying vec3 vColor;
        varying float vFiring;
        void main() {
            int idx = int(aRegion + 0.5);
            // clamped lookup into region firing rate
            float f = 0.0;
            for (int k = 0; k < ${REGION_COUNT}; k++) {
                if (k == idx) f = uFiring[k];
            }
            vFiring = f;
            vColor  = color;
            vFlicker = 0.55 + 0.45 * sin(uTime * 4.0 + position.x * 7.0 + position.z * 5.0 + aRegion * 2.1);
            vec4 mv = modelViewMatrix * vec4(position, 1.0);
            gl_Position = projectionMatrix * mv;
            // Size grows with firing rate + reactive level
            gl_PointSize = size * (300.0 / -mv.z) * (1.0 + uReactive * 0.3 + f * 6.0);
        }
    `,
    fragmentShader: `
        varying vec3 vColor;
        varying float vFlicker;
        varying float vFiring;
        void main() {
            vec2 c = gl_PointCoord - 0.5;
            float d = length(c);
            if (d > 0.5) discard;
            // Softer falloff + a wider inner core so each neuron reads as a
            // luminescent orb instead of a tiny dot. Color is boosted and the
            // alpha curve is flatter so quiet neurons still glow visibly —
            // this is what brings Jarvis up to the Brain viz's brightness.
            float core = smoothstep(0.5, 0.05, d);
            float halo = smoothstep(0.5, 0.25, d);
            vec3 col = vColor * (1.15 + vFiring * 3.2);
            float a = (core * 0.55 + halo * 0.55) * (vFlicker * (0.85 + vFiring * 2.6));
            gl_FragColor = vec4(col, a);
        }
    `,
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
});
const neuralNodes = new THREE.Points(nodeGeo, nodeMat);
scene.add(neuralNodes);

// ---- Brain-region HTML labels ----
// Each label is a small pill-box positioned via 3D→2D projection each frame.
const regionLabelsEl = document.getElementById('region-labels');
const regionLabels = {};
BRAIN_REGIONS.forEach(r => {
    const wrap = document.createElement('div');
    wrap.className = `region-label region-${r.id}`;
    wrap.style.setProperty('--rcolor', `rgb(${Math.round(r.color.r*255)}, ${Math.round(r.color.g*255)}, ${Math.round(r.color.b*255)})`);
    const title = document.createElement('div');
    title.className = 'rl-title';
    title.textContent = r.label;
    const stats = document.createElement('div');
    stats.className = 'rl-stats';
    wrap.appendChild(title);
    wrap.appendChild(stats);
    regionLabelsEl.appendChild(wrap);
    regionLabels[r.id] = { wrap, stats, region: r };
});

// Updates label positions (screen-space) and firing-% text every frame
const _projVec = new THREE.Vector3();
function updateRegionLabels() {
    const w = renderer.domElement.clientWidth;
    const h = renderer.domElement.clientHeight;
    for (const r of BRAIN_REGIONS) {
        const lbl = regionLabels[r.id];
        // Account for mesh rotation when projecting cluster center
        _projVec.copy(r.pos).applyMatrix4(neuralMesh.matrixWorld);
        _projVec.project(camera);
        // project() returns NDC [-1, 1]. Convert to pixel coords.
        const sx = (_projVec.x * 0.5 + 0.5) * w;
        const sy = (-_projVec.y * 0.5 + 0.5) * h;
        const onscreen = _projVec.z < 1 && sx > -50 && sx < w + 50 && sy > -30 && sy < h + 30;
        lbl.wrap.style.transform = `translate(${Math.round(sx)}px, ${Math.round(sy)}px)`;
        lbl.wrap.style.opacity = onscreen ? '' : '0';
        // Update firing % + neuron count
        const firing = regionState[r.id].firing;
        const pct = (firing * 100).toFixed(1);
        const countLabel = r.count > 999 ? (r.count/1000).toFixed(1)+'K' : r.count;
        lbl.stats.textContent = `${countLabel} neurons · firing ${pct}%`;
        // Active glow when firing rate is elevated
        if (firing > 0.12) lbl.wrap.classList.add('active');
        else lbl.wrap.classList.remove('active');
    }
}

// --- Dendrite filaments: curved tubes from core outward ---
const DENDRITE_COUNT = 48;
const dendrites = [];
for (let i = 0; i < DENDRITE_COUNT; i++) {
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    const dir = new THREE.Vector3(
        Math.sin(phi) * Math.cos(theta),
        Math.sin(phi) * Math.sin(theta),
        Math.cos(phi)
    );
    const start = dir.clone().multiplyScalar(1.0);
    const end = dir.clone().multiplyScalar(2.4 + Math.random() * 1.4);
    // perpendicular offset for bend
    const perp1 = new THREE.Vector3().randomDirection();
    const perp2 = new THREE.Vector3().randomDirection();
    const mid1 = start.clone().lerp(end, 0.35).add(perp1.multiplyScalar((Math.random() - 0.5) * 0.9));
    const mid2 = start.clone().lerp(end, 0.7).add(perp2.multiplyScalar((Math.random() - 0.5) * 0.9));
    const curve = new THREE.CatmullRomCurve3([start, mid1, mid2, end]);
    const tubeGeo = new THREE.TubeGeometry(curve, 48, 0.006 + Math.random() * 0.004, 6, false);
    const tubeMat = new THREE.MeshBasicMaterial({
        color: 0x5ec5e0,
        transparent: true,
        opacity: 0.28,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
    });
    const tube = new THREE.Mesh(tubeGeo, tubeMat);
    scene.add(tube);
    dendrites.push({ curve, tube });
}

// --- Signal pulses: bright points traveling along dendrites ---
const PULSES_PER_DENDRITE = 2;
const pulseCount = DENDRITE_COUNT * PULSES_PER_DENDRITE;
const pulseParams = [];
const pulseArr = new Float32Array(pulseCount * 3);
const pulseSizeArr = new Float32Array(pulseCount);
for (let i = 0; i < pulseCount; i++) {
    const dendrite = dendrites[i % DENDRITE_COUNT];
    const dir = Math.random() < 0.7 ? 1 : -1;  // mostly outward, some inward
    pulseParams.push({
        dendrite,
        t: Math.random(),
        speed: (0.2 + Math.random() * 0.45) * dir,
    });
    const p = dendrite.curve.getPoint(pulseParams[i].t);
    pulseArr[i * 3] = p.x; pulseArr[i * 3 + 1] = p.y; pulseArr[i * 3 + 2] = p.z;
    pulseSizeArr[i] = 0.08 + Math.random() * 0.08;
}
const pulseGeo = new THREE.BufferGeometry();
pulseGeo.setAttribute('position', new THREE.BufferAttribute(pulseArr, 3));
pulseGeo.setAttribute('size', new THREE.BufferAttribute(pulseSizeArr, 1));
const pulseMat = new THREE.ShaderMaterial({
    uniforms: {
        uColor: { value: new THREE.Color(0xffffff) },
        uReactive: { value: 0 },
    },
    vertexShader: `
        attribute float size;
        uniform float uReactive;
        void main() {
            vec4 mv = modelViewMatrix * vec4(position, 1.0);
            gl_Position = projectionMatrix * mv;
            gl_PointSize = size * (400.0 / -mv.z) * (1.0 + uReactive * 0.6);
        }
    `,
    fragmentShader: `
        uniform vec3 uColor;
        void main() {
            vec2 c = gl_PointCoord - 0.5;
            float d = length(c);
            if (d > 0.5) discard;
            float core = smoothstep(0.15, 0.0, d);
            float halo = smoothstep(0.5, 0.15, d) * 0.6;
            gl_FragColor = vec4(uColor, core + halo);
        }
    `,
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
});
const pulses = new THREE.Points(pulseGeo, pulseMat);
scene.add(pulses);

// ========================================================================
//   THOUGHT ORBS — live visualization of Jarvis's actual cognition.
//   Each tool call / input fires a WS "thought" event. We spawn a small
//   glowing orb at the core that flies outward along a random neural pathway
//   (a dendrite), leaving a faint trail, then fades out. Color = kind.
// ========================================================================
const THOUGHT_COLORS = {
    input:     new THREE.Color(0x8ac8dd),  // pale cyan — user input
    task:      new THREE.Color(0xffb45e),  // amber — tasks
    note:      new THREE.Color(0x5ee0c8),  // teal — notes
    memory:    new THREE.Color(0xc8a0ff),  // violet — brain/memory
    directive: new THREE.Color(0xff7070),  // red — self-modification
    brain:     new THREE.Color(0xffffff),  // white — brain file I/O
    web:       new THREE.Color(0x78e69a),  // green — web / browser
    vision:    new THREE.Color(0x8ad8ec),  // light blue — screen vision
    code:      new THREE.Color(0xffa050),  // orange — dev / code
    shell:     new THREE.Color(0xff8030),  // deeper orange — shell
    system:    new THREE.Color(0xff4050),  // hot red — unscoped system ops
    control:   new THREE.Color(0xffea60),  // yellow — mouse/keyboard control
    tool:      new THREE.Color(0xbfeaff),  // default
};

const thoughtOrbs = [];  // active orbs being animated
const THOUGHT_ORB_GEO = new THREE.SphereGeometry(0.06, 16, 16);

function spawnThoughtOrb(kind, text) {
    // Which brain region owns this thought-kind?
    const regionId = KIND_TO_REGION[kind] || FALLBACK_REGION_ID;
    const region = BRAIN_REGIONS.find(r => r.id === regionId);

    // Spike the firing rate of that region — it flares for a moment, then decays
    if (region && regionState[region.id]) {
        regionState[region.id].firing = Math.min(1.0, regionState[region.id].firing + 0.55);
        regionState[region.id].lastSpike = clock.elapsedTime;
    }

    // Broadcast the cortex activation to any parent window (the ops-center
    // dashboard) so the NeuroLinked Brain iframe can light up the matching
    // region in the same color at the same instant. Safe no-op when Jarvis
    // runs standalone (window.parent === window).
    try {
        if (region && window.parent && window.parent !== window) {
            window.parent.postMessage({
                type: 'jarvis_cortex_fire',
                region_id:   region.id,
                brain_id:    region.brain_id || region.id,
                kind:        kind,
                color_hex:   '#' + region.color.getHexString(),
                label:       region.label,
                text:        (text || '').slice(0, 140),
                ts:          Date.now(),
            }, '*');
        }
    } catch (_e) { /* cross-origin or no parent — ignore */ }

    // Use the region color when available, else the kind color palette
    const baseColor = region ? region.color.clone() : (THOUGHT_COLORS[kind] || THOUGHT_COLORS.tool).clone();
    const material = new THREE.MeshBasicMaterial({
        color: baseColor,
        transparent: true,
        opacity: 1.0,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
    });
    const orb = new THREE.Mesh(THOUGHT_ORB_GEO, material);

    // Orb travels along a dendrite but STARTS near the source region's cluster
    // (then flies outward along the dendrite, fading). We offset the orb's animated
    // position by (region.pos - dendrite.start) so the neural activity looks like it
    // originates from the matching region.
    const dendrite = dendrites[Math.floor(Math.random() * dendrites.length)];
    const duration = 1.6 + Math.random() * 1.0;

    scene.add(orb);
    thoughtOrbs.push({
        orb,
        material,
        dendrite,
        regionPos: region ? region.pos.clone() : new THREE.Vector3(0, 0, 0),
        startTime: clock.elapsedTime,
        duration,
        color: baseColor,
    });

    // Also push to the side feed (HTML)
    pushThoughtFeed(kind, text);
}

function updateThoughtOrbs(t) {
    for (let i = thoughtOrbs.length - 1; i >= 0; i--) {
        const th = thoughtOrbs[i];
        const elapsed = t - th.startTime;
        const u = elapsed / th.duration;
        if (u >= 1) {
            scene.remove(th.orb);
            th.material.dispose();
            thoughtOrbs.splice(i, 1);
            continue;
        }
        // First half: orb lives at its source region and grows bright.
        // Second half: orb travels outward along a dendrite, as if the signal
        // leaves that region toward the edge of the brain.
        const eased = 1 - Math.pow(1 - u, 2.2);
        let pos;
        if (u < 0.35) {
            // Hover at the region (with a small dance so it doesn't look dead)
            const jx = (Math.random() - 0.5) * 0.03;
            const jy = (Math.random() - 0.5) * 0.03;
            const jz = (Math.random() - 0.5) * 0.03;
            pos = th.regionPos.clone().add(new THREE.Vector3(jx, jy, jz));
        } else {
            // Lerp from region pos outward toward dendrite endpoint
            const segU = (u - 0.35) / 0.65;
            const dendriteEnd = th.dendrite.curve.getPoint(Math.min(0.95, segU));
            pos = th.regionPos.clone().lerp(dendriteEnd, Math.pow(segU, 1.3));
        }
        th.orb.position.copy(pos);
        // Fade + size pulse
        const fade = Math.pow(1 - u, 1.4);
        th.material.opacity = 0.3 + 0.7 * fade;
        const scale = 1.0 + 1.8 * Math.sin(u * Math.PI) * (1 + reactiveLevel * 0.5);
        th.orb.scale.setScalar(scale);
    }
}

// ---- Side feed (HTML overlay) ----
const thoughtFeed = document.getElementById('thought-feed');
function pushThoughtFeed(kind, text) {
    if (!thoughtFeed) return;
    const div = document.createElement('div');
    div.className = `thought-item kind-${kind}`;
    div.innerHTML = `<span class="thought-kind">${kind}</span><span class="thought-text">${escapeHtml(text)}</span>`;
    thoughtFeed.appendChild(div);
    // keep max 15 entries
    while (thoughtFeed.children.length > 15) {
        thoughtFeed.removeChild(thoughtFeed.firstChild);
    }
    // auto-scroll to bottom
    thoughtFeed.scrollTop = thoughtFeed.scrollHeight;
    // auto-fade-out after 10s
    setTimeout(() => {
        div.classList.add('fading');
        setTimeout(() => div.remove(), 1200);
    }, 10000);
}
function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
}

// --- Ambient particle field (brain dust) ---
const particleCount = 900;
const particleGeo = new THREE.BufferGeometry();
const positions = new Float32Array(particleCount * 3);
const sizes = new Float32Array(particleCount);
for (let i = 0; i < particleCount; i++) {
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    const r = 3.5 + Math.random() * 4.5;
    positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    positions[i * 3 + 2] = r * Math.cos(phi);
    sizes[i] = Math.random() * 0.08 + 0.02;
}
particleGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
particleGeo.setAttribute('size', new THREE.BufferAttribute(sizes, 1));

const particleMat = new THREE.ShaderMaterial({
    uniforms: {
        uTime: { value: 0 },
        uReactive: { value: 0 },
        uColor: { value: new THREE.Color(0x8ef0ff) },
    },
    vertexShader: `
        attribute float size;
        uniform float uTime;
        uniform float uReactive;
        varying float vAlpha;
        void main() {
            vec3 pos = position;
            float angle = uTime * 0.05;
            float c = cos(angle), s = sin(angle);
            pos.xz = mat2(c, -s, s, c) * pos.xz;
            vec4 mv = modelViewMatrix * vec4(pos, 1.0);
            gl_Position = projectionMatrix * mv;
            gl_PointSize = size * (300.0 / -mv.z) * (1.0 + uReactive * 0.8);
            vAlpha = 0.4 + 0.6 * sin(uTime * 2.0 + position.x * 3.0);
        }
    `,
    fragmentShader: `
        uniform vec3 uColor;
        varying float vAlpha;
        void main() {
            vec2 c = gl_PointCoord - 0.5;
            float d = length(c);
            if (d > 0.5) discard;
            float glow = smoothstep(0.5, 0.0, d);
            gl_FragColor = vec4(uColor, glow * vAlpha);
        }
    `,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
});
const particles = new THREE.Points(particleGeo, particleMat);
scene.add(particles);

// --- Background: depth vortex (concentric rings receding into space) ---
const vortexGroup = new THREE.Group();
const VORTEX_RINGS = 22;
for (let i = 0; i < VORTEX_RINGS; i++) {
    const r = 4 + i * 0.9;
    const geo = new THREE.RingGeometry(r, r + 0.015, 96, 1);
    const mat = new THREE.MeshBasicMaterial({
        color: 0x5ec5e0,
        transparent: true,
        opacity: 0.08 + (1 - i / VORTEX_RINGS) * 0.08,
        side: THREE.DoubleSide,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
    });
    const ring = new THREE.Mesh(geo, mat);
    ring.position.z = -i * 1.1 - 2;
    ring.rotation.x = (Math.random() - 0.5) * 0.3;
    ring.rotation.y = (Math.random() - 0.5) * 0.3;
    ring.userData.baseOpacity = mat.opacity;
    ring.userData.rotSpeed = (Math.random() - 0.5) * 0.4;
    vortexGroup.add(ring);
}
scene.add(vortexGroup);

// --- Post-processing: bloom + chromatic aberration + glitch jitter + FXAA ---
const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
// Strength / radius / threshold — bumped so Jarvis's 3D brain matches the
// luminous look of the NeuroLinked Brain viz below it.
const bloomPass = new UnrealBloomPass(
    new THREE.Vector2(window.innerWidth, window.innerHeight),
    0.95, 0.9, 0.18
);
composer.addPass(bloomPass);

// Chromatic aberration + subtle rolling glitch scanlines
const ChromaticGlitchShader = {
    uniforms: {
        tDiffuse: { value: null },
        uReactive: { value: 0 },
        uTime: { value: 0 },
        uGlitch: { value: 0 },
        uAspect: { value: window.innerWidth / window.innerHeight },
    },
    vertexShader: `
        varying vec2 vUv;
        void main() {
            vUv = uv;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
    `,
    fragmentShader: `
        uniform sampler2D tDiffuse;
        uniform float uReactive;
        uniform float uTime;
        uniform float uGlitch;
        uniform float uAspect;
        varying vec2 vUv;

        float hash(vec2 p) {
            return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
        }

        void main() {
            vec2 uv = vUv;
            vec2 center = vec2(0.5, 0.5);
            vec2 dir = uv - center;

            // radial chromatic aberration — stronger at edges and with audio
            float strength = 0.0025 + uReactive * 0.012 + uGlitch * 0.02;
            vec2 rOff =  dir * strength;
            vec2 bOff = -dir * strength;

            // horizontal tear bands that come and go
            float band = step(0.985, hash(vec2(floor(uv.y * 120.0), floor(uTime * 4.0))));
            float tear = band * uGlitch * 0.04;
            uv.x += tear * (hash(vec2(floor(uv.y * 60.0), floor(uTime * 8.0))) - 0.5) * 2.0;

            float r = texture2D(tDiffuse, uv + rOff).r;
            float g = texture2D(tDiffuse, uv).g;
            float b = texture2D(tDiffuse, uv + bOff).b;

            // scanlines
            float scan = 0.94 + 0.06 * sin(uv.y * 800.0 + uTime * 2.0);

            // vignette
            float v = 1.0 - smoothstep(0.55, 1.1, length(dir * vec2(uAspect, 1.0)));

            vec3 col = vec3(r, g, b) * scan * (0.85 + 0.2 * v);
            gl_FragColor = vec4(col, 1.0);
        }
    `,
};
const chromaticPass = new ShaderPass(ChromaticGlitchShader);
composer.addPass(chromaticPass);

const fxaaPass = new ShaderPass(FXAAShader);
fxaaPass.material.uniforms['resolution'].value.set(1 / window.innerWidth, 1 / window.innerHeight);
composer.addPass(fxaaPass);

// --- Color targets per state (smoothly interpolated) ---
// Palette: clinical blue/white (Jarvis) + deep violet (Brain speaking) — Neurolink aesthetic
const STATE_COLORS = {
    idle:      { a: new THREE.Color(0x14283a), b: new THREE.Color(0x5ec5e0), neural: new THREE.Color(0x7ac8dd), node: new THREE.Color(0xbfeaff), part: new THREE.Color(0x9fdcef), pulse: new THREE.Color(0xffffff), emissive: 0.45 },
    listening: { a: new THREE.Color(0x2a6ea0), b: new THREE.Color(0x8ad8ec), neural: new THREE.Color(0xaee4f2), node: new THREE.Color(0xffffff), part: new THREE.Color(0xcfefff), pulse: new THREE.Color(0xffffff), emissive: 0.7 },
    thinking:  { a: new THREE.Color(0x3a5fb0), b: new THREE.Color(0x8ab0ea), neural: new THREE.Color(0xaec5f5), node: new THREE.Color(0xd0e0ff), part: new THREE.Color(0xbcd0ff), pulse: new THREE.Color(0xeaf2ff), emissive: 0.7 },
    speaking:  { a: new THREE.Color(0x2a7ea0), b: new THREE.Color(0x78d8ec), neural: new THREE.Color(0x9ee8ef), node: new THREE.Color(0xd8ffff), part: new THREE.Color(0xc0f5fc), pulse: new THREE.Color(0xeaffff), emissive: 0.85 },
    brain:     { a: new THREE.Color(0x3a1a5c), b: new THREE.Color(0xa870e8), neural: new THREE.Color(0xc8a0ff), node: new THREE.Color(0xf0d8ff), part: new THREE.Color(0xd8b0ff), pulse: new THREE.Color(0xfff0ff), emissive: 1.0 },
};

let currentColor = {
    a: STATE_COLORS.idle.a.clone(),
    b: STATE_COLORS.idle.b.clone(),
    neural: STATE_COLORS.idle.neural.clone(),
    node: STATE_COLORS.idle.node.clone(),
    part: STATE_COLORS.idle.part.clone(),
    pulse: STATE_COLORS.idle.pulse.clone(),
    emissive: STATE_COLORS.idle.emissive,
};

// --- Animation loop ---
const clock = new THREE.Clock();
function animate() {
    requestAnimationFrame(animate);
    const dt = Math.min(clock.getDelta(), 0.05);
    const t = clock.elapsedTime;

    // sample audio sources
    micLevel += (sampleMic() - micLevel) * 0.3;
    ttsLevel += (sampleTts() - ttsLevel) * 0.3;

    // pick which source drives visuals
    const active = currentState === STATE.SPEAKING ? ttsLevel
                 : currentState === STATE.LISTENING ? micLevel
                 : currentState === STATE.THINKING ? (0.3 + 0.25 * Math.sin(t * 4))
                 : 0.1 + 0.05 * Math.sin(t * 1.5);
    reactiveLevel += (active - reactiveLevel) * 0.2;

    // update mic meter HUD
    micBar.style.width = `${Math.round(micLevel * 100)}%`;

    // interpolate colors toward state target
    const target = STATE_COLORS[currentState];
    currentColor.a.lerp(target.a, 0.05);
    currentColor.b.lerp(target.b, 0.05);
    currentColor.neural.lerp(target.neural, 0.05);
    currentColor.node.lerp(target.node, 0.05);
    currentColor.part.lerp(target.part, 0.05);
    currentColor.pulse.lerp(target.pulse, 0.05);
    currentColor.emissive += (target.emissive - currentColor.emissive) * 0.05;

    // core — Z.E.R.O.'s presence. Layered pulsation:
    //   - slow breath (0.6 rad/s ≈ 5.2s/cycle, 6% amplitude) — visible "alive" rhythm
    //   - inner heartbeat (1.7 rad/s, 1.5%) — subtle texture so it never feels mechanical
    //   - reactive boost (when speaking) — preserved
    // Emissive ALSO breathes so the core visibly brightens/dims with each pulse.
    coreUniforms.uTime.value = t;
    coreUniforms.uReactive.value = reactiveLevel;
    coreUniforms.uColorA.value.copy(currentColor.a);
    coreUniforms.uColorB.value.copy(currentColor.b);
    const breathPhase = t * 0.6;
    const breath = Math.sin(breathPhase);              // -1 .. 1
    const breath01 = (breath + 1) * 0.5;               //  0 .. 1
    coreUniforms.uEmissive.value = currentColor.emissive * (0.78 + breath01 * 0.32);
    core.rotation.y = t * 0.12;
    core.rotation.x = Math.sin(t * 0.18) * 0.1;
    const coreScale =
        1
        + breath * 0.060            // slow breath ±6%
        + Math.sin(t * 1.7) * 0.015 // inner heartbeat ±1.5%
        + reactiveLevel * 0.20;     // reactive boost when speaking
    core.scale.set(coreScale, coreScale, coreScale);

    // 4D tesseract: rotate in 4D + project
    updateTesseract(t, reactiveLevel);
    tesseractMat.uniforms.uTime.value = t;
    tesseractMat.uniforms.uReactive.value = reactiveLevel;
    tesseractMat.uniforms.uColor.value.copy(currentColor.node);
    tesseract.rotation.y = t * 0.25;
    tesseract.rotation.x = t * 0.15;
    tesseract.scale.setScalar(0.55 + reactiveLevel * 0.2 + Math.sin(t * 2.1) * 0.03);

    // Depth vortex: swirl and creep forward
    vortexGroup.rotation.z = t * 0.15;
    vortexGroup.children.forEach((ring, i) => {
        ring.rotation.z += ring.userData.rotSpeed * dt;
        ring.position.z += dt * 0.3 * (1 + reactiveLevel * 1.5);
        if (ring.position.z > 2) ring.position.z = -VORTEX_RINGS * 1.1 - 2;
        ring.material.opacity = ring.userData.baseOpacity * (0.5 + 0.5 * Math.sin(t * 0.6 + i * 0.4)) * (0.6 + reactiveLevel * 0.8);
        ring.material.color.copy(currentColor.neural);
    });

    // inner shell
    wireShell.rotation.y = -t * 0.08;
    wireShell.rotation.x = t * 0.04;
    wireShell.scale.setScalar(1 + reactiveLevel * 0.1);
    wireMat.opacity = 0.05 + reactiveLevel * 0.2;

    // neural mesh
    // Decay per-region firing rates back toward baseline each frame
    for (const r of BRAIN_REGIONS) {
        const s = regionState[r.id];
        const baseline = 0.008;
        // Decay toward baseline with a half-life ~1 second
        s.firing += (baseline - s.firing) * (1 - Math.exp(-dt * 1.5));
        // Write to uniform array indexed by region order
        regionFiringUniform[BRAIN_REGIONS.indexOf(r)] = s.firing;
    }
    nodeMat.uniforms.uFiring.value = regionFiringUniform;

    meshMat.uniforms.uTime.value = t;
    meshMat.uniforms.uReactive.value = reactiveLevel;
    // (color uniform removed — mesh now uses per-vertex colors)
    neuralMesh.rotation.y = t * 0.05;
    neuralMesh.rotation.x = Math.sin(t * 0.07) * 0.15;

    // neural nodes (glowing dots)
    nodeMat.uniforms.uTime.value = t;
    nodeMat.uniforms.uReactive.value = reactiveLevel;
    // (uColor/node-color is now per-vertex and baked in)
    neuralNodes.rotation.copy(neuralMesh.rotation);

    // dendrite tubes — color + subtle opacity breath
    dendrites.forEach(d => {
        d.tube.material.color.copy(currentColor.neural);
        d.tube.material.opacity = 0.2 + reactiveLevel * 0.4;
    });

    // signal pulses travel along dendrite curves
    const pa = pulseGeo.attributes.position.array;
    for (let i = 0; i < pulseCount; i++) {
        const p = pulseParams[i];
        p.t += p.speed * dt * (1 + reactiveLevel * 1.8);
        if (p.t > 1) p.t -= 1;
        if (p.t < 0) p.t += 1;
        const pt = p.dendrite.curve.getPoint(p.t);
        pa[i * 3] = pt.x;
        pa[i * 3 + 1] = pt.y;
        pa[i * 3 + 2] = pt.z;
    }
    pulseGeo.attributes.position.needsUpdate = true;
    pulseMat.uniforms.uReactive.value = reactiveLevel;
    pulseMat.uniforms.uColor.value.copy(currentColor.pulse);

    // animate live thought orbs
    updateThoughtOrbs(t);

    // reposition + update brain-region HTML labels
    updateRegionLabels();

    // ambient particles
    particleMat.uniforms.uTime.value = t;
    particleMat.uniforms.uReactive.value = reactiveLevel;
    particleMat.uniforms.uColor.value.copy(currentColor.part);

    // camera drift
    camera.position.x = Math.sin(t * 0.1) * 0.3;
    camera.position.y = Math.cos(t * 0.08) * 0.2;
    camera.lookAt(0, 0, 0);

    // bloom intensity reacts
    bloomPass.strength = 0.85 + reactiveLevel * 0.55;

    // glitch driver — random spikes + voice-peak bursts, decays over time
    glitchLevel = Math.max(0, glitchLevel - dt * 1.4);
    if (reactiveLevel > 0.55 && Math.random() < 0.08) glitchLevel = Math.max(glitchLevel, 0.6);
    if (Math.random() < 0.003) glitchLevel = Math.max(glitchLevel, 0.45); // ambient glitch
    chromaticPass.uniforms.uTime.value = t;
    chromaticPass.uniforms.uReactive.value = reactiveLevel;
    chromaticPass.uniforms.uGlitch.value = glitchLevel;

    composer.render();
}
animate();

window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
    composer.setSize(window.innerWidth, window.innerHeight);
    fxaaPass.material.uniforms['resolution'].value.set(1 / window.innerWidth, 1 / window.innerHeight);
    chromaticPass.uniforms.uAspect.value = window.innerWidth / window.innerHeight;
});

// ========================================================================
//   CLOCK
// ========================================================================
function updateClock() {
    const d = new Date();
    $('clock').textContent = d.toLocaleTimeString('en-US', { hour12: false });
}
setInterval(updateClock, 1000);
updateClock();

// ========================================================================
//   WEBSOCKET + AUDIO PLAYBACK
// ========================================================================
let ws;
let audioQueue = [];
let isPlaying = false;
let audioUnlocked = false;

function unlockAudio() {
    if (!audioUnlocked) {
        if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        if (audioCtx.state === 'suspended') audioCtx.resume();
        const silent = new Audio('data:audio/mp3;base64,SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4Ljc2LjEwMAAAAAAAAAAAAAAA//tQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWGluZwAAAA8AAAACAAABhgC7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7//////////////////////////////////////////////////////////////////8AAAAATGF2YzU4LjEzAAAAAAAAAAAAAAAAJAAAAAAAAAAAAYZNIGPkAAAAAAAAAAAAAAAAAAAA');
        silent.play().then(() => {
            audioUnlocked = true;
            hintText.textContent = 'Audio armed';
            initMicAnalyser();
            document.body.classList.add('booting');
            setTimeout(() => document.body.classList.remove('booting'), 400);
        }).catch(() => {});
    }
}

// Launch token, injected into the served HTML by the server. Used as a query
// param on the WebSocket connect and as an X-Neurolinked-Token header on every
// fetch. Without this, /api/* and /ws return 401 even from same-origin pages.
const __TOKEN = (typeof window !== 'undefined' && window.__NEUROLINKED_TOKEN__) || '';
function authedFetch(url, init = {}) {
    const headers = new Headers(init.headers || {});
    if (__TOKEN) headers.set('X-Neurolinked-Token', __TOKEN);
    return fetch(url, { ...init, headers });
}

// Stable session ID. Persists in localStorage so a tab refresh, network blip,
// or browser sleep/wake doesn't lose Jarvis's conversation context. Without
// this, every reconnect spun up a fresh `str(id(ws))` session and the LLM
// forgot what we were doing. Same ID = same conversation history.
function _getStableSessionId() {
    try {
        let sid = localStorage.getItem('neurolinked_sid');
        if (!sid) {
            // 16-byte random hex — collision-resistant, no PII.
            const a = new Uint8Array(16);
            (window.crypto || window.msCrypto).getRandomValues(a);
            sid = Array.from(a, b => b.toString(16).padStart(2, '0')).join('');
            localStorage.setItem('neurolinked_sid', sid);
        }
        return sid;
    } catch (_) {
        // Private mode or storage disabled — fall back to per-page-load id
        return 'ephem-' + Math.random().toString(36).slice(2);
    }
}
const __SID = _getStableSessionId();

function connect() {
    const tok = encodeURIComponent(__TOKEN);
    const sid = encodeURIComponent(__SID);
    ws = new WebSocket(`ws://${location.host}/ws?token=${tok}&sid=${sid}`);
    ws.onopen = () => {
        setState(STATE.IDLE);
        hintText.textContent = 'Say "Hey Zero" or tap SPACE';
    };
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'response') {
            const source = data.source || 'jarvis';
            // If the user interrupted the LAST turn, drop late audio chunks
            // that the server is still sending for that cancelled turn —
            // otherwise we'd hear two voices at once.
            if (_suppressLateAudio) {
                addTranscript(source === 'brain' ? 'brain' : 'jarvis', data.text);
                return;
            }
            addTranscript(source === 'brain' ? 'brain' : 'jarvis', data.text);
            if (data.audio && data.audio.length > 0) {
                const audioFmt = data.audio_format || 'mp3';
                queueAudio(data.audio, source, audioFmt);
            } else if (data.text && data.text.trim()) {
                // No audio came back (user chose browser TTS, OR ElevenLabs
                // failed — e.g. quota exhausted). Speak via the browser's
                // built-in synthesis so Jarvis never goes silent.
                speakViaBrowser(data.text, source);
            } else {
                setState(STATE.IDLE);
                hintText.textContent = 'Say "Hey Zero" or tap SPACE';
            }
        } else if (data.type === 'thought') {
            // Server signaled that a cognition event just happened — visualize it
            spawnThoughtOrb(data.kind || 'tool', data.text || '');
        } else if (data.type === 'request_frame') {
            // Server wants a webcam frame on demand (see_me tool)
            const frame = captureWebcamFrame();
            if (frame) {
                flashWebcamEye();
                ws.send(JSON.stringify({ type: 'frame_response', frame }));
            } else {
                ws.send(JSON.stringify({ type: 'frame_response', frame: null }));
            }
        }
    };
    ws.onclose = () => {
        hintText.textContent = 'Connection lost · reconnecting...';
        setState(STATE.IDLE);
        setTimeout(connect, 3000);
    };
}

// Browser-native speech synthesis fallback — used when the backend returns no
// audio (tts_provider=browser, or ElevenLabs quota/key failed). Picks the best
// English voice available and matches the Jarvis/brain tone: slightly slower
// and lower pitch for Jarvis, slower still for Brain.
function speakViaBrowser(text, source = 'jarvis') {
    if (!('speechSynthesis' in window)) {
        setState(STATE.IDLE);
        hintText.textContent = 'Say "Hey Zero" or tap SPACE';
        return;
    }
    try { window.speechSynthesis.cancel(); } catch (_) {}
    const u = new SpeechSynthesisUtterance(text);
    u.lang = 'en-US';
    u.rate  = source === 'brain' ? 0.92 : 1.0;
    u.pitch = source === 'brain' ? 0.85 : 0.95;
    u.volume = 1.0;
    // Prefer a natural-sounding English voice if the browser has one loaded.
    const voices = window.speechSynthesis.getVoices() || [];
    const prefer = voices.find(v => /Google UK English Male|Microsoft Guy|Daniel|Microsoft David/i.test(v.name))
               || voices.find(v => /en-GB/i.test(v.lang))
               || voices.find(v => /en-US/i.test(v.lang));
    if (prefer) u.voice = prefer;
    setState(source === 'brain' ? STATE.BRAIN : STATE.SPEAKING);
    isPlaying = true;
    u.onend = u.onerror = () => {
        isPlaying = false;
        setState(STATE.IDLE);
        hintText.textContent = 'Say "Hey Zero" or tap SPACE';
    };
    try { window.speechSynthesis.speak(u); } catch (_) {
        isPlaying = false;
        setState(STATE.IDLE);
    }
}

function queueAudio(b64, source = 'jarvis', audioFormat = 'mp3') {
    audioQueue.push({ b64, source, audioFormat });
    if (!isPlaying) playNext();
}

function playNext() {
    if (audioQueue.length === 0) {
        isPlaying = false;
        setState(STATE.IDLE);
        hintText.textContent = 'Say "Hey Zero" or tap SPACE';
        return;
    }
    isPlaying = true;
    const item = audioQueue.shift();
    // Switch to Brain state (violet) if this audio came from the Brain, otherwise normal SPEAKING
    setState(item.source === 'brain' ? STATE.BRAIN : STATE.SPEAKING);
    // DEFENSIVELY pause listening — Jarvis never hears himself. We don't
    // abort the engine (that would kill the wake-word listener); instead we
    // just drop out of ACTIVE mode and the onresult handler ignores input
    // while isPlaying is true.
    activeMode = false;
    lastInterim = '';
    liveTranscript.textContent = '';
    const b64 = item.b64;
    const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    const fmt = item.audioFormat || 'mp3';
    const mimeType = fmt === 'wav' ? 'audio/wav' : 'audio/mpeg';
    const blob = new Blob([bytes], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.crossOrigin = 'anonymous';
    audio.addEventListener('play', () => {
        attachTtsAnalyser(audio);
        // Start watching the mic for user barge-in. The check loop pauses
        // Jarvis the moment sustained voice is detected over his playback.
        startVadDuringPlayback();
    }, { once: true });
    audio.onended = () => { URL.revokeObjectURL(url); playNext(); };
    audio.onerror = () => { URL.revokeObjectURL(url); playNext(); };
    audio.play().catch(() => {
        hintText.textContent = 'Click anywhere to enable audio';
        setState(STATE.IDLE);
        document.addEventListener('click', function retry() {
            document.removeEventListener('click', retry);
            audio.play().then(() => setState(item.source === 'brain' ? STATE.BRAIN : STATE.SPEAKING)).catch(() => playNext());
        }, { once: true });
    });
}

// ========================================================================
//   SPEECH RECOGNITION — wake-word + push-to-talk
// ------------------------------------------------------------------------
// Two activation paths share one continuous recognition stream:
//   1. WAKE WORD  → user says "hey jarvis" → enter ACTIVE for 10s
//   2. PUSH-TO-TALK → user taps SPACE → enter ACTIVE for 10s
// ACTIVE = the next final utterance is sent to the server. If 10 seconds
// pass with no new speech, we drop back to PASSIVE (recognition still
// running in the background, scanning for the wake word again).
// ========================================================================
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition;
let recognitionStarted = false;     // is the engine running at all?
let activeMode = false;             // are we accepting the next utterance as a query?
let activeDeadline = 0;             // ms epoch when ACTIVE auto-closes
let activeTimer = null;             // setTimeout handle for the deadline
let pushToTalk = false;             // SPACE currently held
let lastInterim = '';               // most recent interim in ACTIVE mode
let utteranceSent = false;          // guards against the doubled-reply bug
let lastWakeAt = 0;                 // debounce: ignore wake events in quick succession

// Wake regex — accept "Hey Zero", "Zero", AND legacy "Hey Jarvis" during transition.
// User is renaming Jarvis -> Z.E.R.O.; legacy phrasing still wakes the assistant
// so old habits don't break flow. New canonical phrase is "Hey Zero".
const WAKE_RE = /\b(hey[,]?\s+)?(zero|jarvis)\b/i;
const ACTIVE_WINDOW_MS = 10000;     // "stay active for about ten seconds"
const WAKE_DEBOUNCE_MS = 1500;      // ignore repeat wake detections inside this window

// ========================================================================
//   BARGE-IN — when user starts talking while Jarvis is speaking, we PAUSE
//   his audio (don't kill it). If real speech comes in within 2 seconds,
//   we kill the paused audio + ignore late chunks for that turn. If the
//   user goes silent — false alarm, lens cap thump, dog bark — we resume
//   right where Jarvis left off. Three triggers fire pauseForBargeIn():
//     - SPACE keydown while audio is playing
//     - "Hey Jarvis" wake word detected during playback
//     - Mic VAD: sustained voice level above threshold during playback
// ========================================================================
let _pausedAudioElems = [];         // <audio> elements paused on barge-in
let _pausedQueueSnapshot = null;    // queued chunks not yet played
let _bargeInResumeTimer = null;
let _suppressLateAudio = false;     // ignore late chunks for cancelled turn
const BARGE_IN_RESUME_MS = 1800;    // 1.8s of silence → resume
const VAD_THRESHOLD = 0.20;         // mic level (0-1) considered "user speaking"
const VAD_HOLD_MS = 220;            // must stay above for this long to count
let _vadCheckId = null;
let _vadAboveThresholdSince = 0;

function pauseForBargeIn() {
    if (!isPlaying && audioQueue.length === 0) return;  // nothing to pause
    // Pause the currently-playing <audio> elements; collect refs to resume.
    _pausedAudioElems = [];
    document.querySelectorAll('audio').forEach((a) => {
        if (!a.paused) {
            try { a.pause(); } catch (_) {}
            _pausedAudioElems.push(a);
        }
    });
    try {
        if ('speechSynthesis' in window) window.speechSynthesis.pause();
    } catch (_) {}
    _pausedQueueSnapshot = audioQueue.slice();
    audioQueue = [];
    isPlaying = false;
    setState(STATE.LISTENING);
    // Auto-resume after silence window.
    if (_bargeInResumeTimer) clearTimeout(_bargeInResumeTimer);
    _bargeInResumeTimer = setTimeout(() => {
        // No real speech came in — resume.
        if (!utteranceSent && !activeMode) {
            // Active mode might still be open; only resume if we're not
            // about to send something.
            resumeFromBargeIn();
        } else if (activeMode && !lastInterim) {
            // Active mode is open but user said nothing — resume.
            resumeFromBargeIn();
            exitActiveMode();
        }
    }, BARGE_IN_RESUME_MS);
}

function resumeFromBargeIn() {
    if (_bargeInResumeTimer) { clearTimeout(_bargeInResumeTimer); _bargeInResumeTimer = null; }
    let resumed = false;
    for (const a of _pausedAudioElems) {
        try { a.play(); resumed = true; } catch (_) {}
    }
    _pausedAudioElems = [];
    if (_pausedQueueSnapshot) {
        // Merge snapshot back to the front of the queue so any new chunks
        // that arrived after barge-in still play in order.
        audioQueue = _pausedQueueSnapshot.concat(audioQueue);
        _pausedQueueSnapshot = null;
    }
    try {
        if ('speechSynthesis' in window) window.speechSynthesis.resume();
    } catch (_) {}
    if (resumed || audioQueue.length > 0) {
        isPlaying = true;
        setState(STATE.SPEAKING);
        if (audioQueue.length > 0 && !resumed) playNext();
    }
}

function killBargeInPaused() {
    // Permanently discard the paused audio — user really did interrupt.
    if (_bargeInResumeTimer) { clearTimeout(_bargeInResumeTimer); _bargeInResumeTimer = null; }
    for (const a of _pausedAudioElems) {
        try { a.pause(); a.currentTime = 0; a.src = ''; } catch (_) {}
    }
    _pausedAudioElems = [];
    _pausedQueueSnapshot = null;
    try {
        if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    } catch (_) {}
    _suppressLateAudio = true;       // drop any late chunks for the cancelled turn
}

// VAD loop: only runs while Jarvis is actually speaking. If sustained mic
// level stays above threshold (= user speaking over him), trigger barge-in.
function startVadDuringPlayback() {
    if (_vadCheckId) return;
    _vadAboveThresholdSince = 0;
    _vadCheckId = setInterval(() => {
        if (!isPlaying) { stopVadDuringPlayback(); return; }
        const lvl = sampleMic();
        if (lvl > VAD_THRESHOLD) {
            if (!_vadAboveThresholdSince) _vadAboveThresholdSince = Date.now();
            else if (Date.now() - _vadAboveThresholdSince > VAD_HOLD_MS) {
                stopVadDuringPlayback();
                pauseForBargeIn();   // user is speaking — pause Jarvis
            }
        } else {
            _vadAboveThresholdSince = 0;
        }
    }, 60);
}
function stopVadDuringPlayback() {
    if (_vadCheckId) { clearInterval(_vadCheckId); _vadCheckId = null; }
    _vadAboveThresholdSince = 0;
}

// Strip the wake phrase from the start/anywhere of a transcript — we only
// want the actual query ("what time is it"), not "hey jarvis what time is it".
function stripWakePhrase(s) {
    return s.replace(WAKE_RE, ' ').replace(/\s+/g, ' ').trim();
}

function scheduleActiveExpiry() {
    if (activeTimer) clearTimeout(activeTimer);
    activeDeadline = Date.now() + ACTIVE_WINDOW_MS;
    activeTimer = setTimeout(() => {
        // 10s passed without a submit — drop back to PASSIVE.
        if (activeMode && !utteranceSent) {
            activeMode = false;
            lastInterim = '';
            liveTranscript.textContent = '';
            if (currentState === STATE.LISTENING) setState(STATE.IDLE);
            hintText.textContent = 'Say "Hey Zero" or tap SPACE';
        }
    }, ACTIVE_WINDOW_MS);
}

function enterActiveMode(trigger = 'space') {
    // If Jarvis is currently speaking, treat this as a barge-in: pause his
    // audio so the user can talk over him without two voices overlapping.
    // The paused audio will be killed if the user actually says something,
    // or resumed if they go silent within BARGE_IN_RESUME_MS.
    if (isPlaying || _pausedAudioElems.length > 0) {
        pauseForBargeIn();
    }
    activeMode = true;
    utteranceSent = false;
    lastInterim = '';
    setState(STATE.LISTENING);
    hintText.textContent = trigger === 'wake'
        ? 'Yes, sir — listening...'
        : 'Listening · release SPACE to send';
    scheduleActiveExpiry();
}

function exitActiveMode() {
    activeMode = false;
    lastInterim = '';
    liveTranscript.textContent = '';
    if (activeTimer) { clearTimeout(activeTimer); activeTimer = null; }
    if (currentState === STATE.LISTENING) setState(STATE.IDLE);
    hintText.textContent = 'Say "Hey Zero" or tap SPACE';
}

function submitUtterance(text) {
    const clean = stripWakePhrase((text || '').trim());
    if (!clean) {
        // User said only the wake phrase (or a filler like "jarvis?") and
        // paused. Keep the 10-second window open so they can actually ask
        // something — do NOT drop back to passive yet.
        lastInterim = '';
        liveTranscript.textContent = '';
        scheduleActiveExpiry();
        hintText.textContent = 'Yes, sir — listening...';
        return;
    }
    utteranceSent = true;
    activeMode = false;
    lastInterim = '';
    liveTranscript.textContent = '';
    if (activeTimer) { clearTimeout(activeTimer); activeTimer = null; }
    // Real interrupt — discard the previous reply we paused, ignore late
    // chunks for the cancelled turn, but DO accept the new turn's audio.
    killBargeInPaused();
    _suppressLateAudio = true;       // drop late chunks of the OLD turn
    addTranscript('user', clean);
    setState(STATE.THINKING);
    hintText.textContent = 'Processing...';
    const frame = captureWebcamFrame();
    if (frame) flashWebcamEye();
    const msg = frame ? { text: clean, frame } : { text: clean };
    // Clear suppression once the NEW message goes out — its response is
    // expected and should play normally.
    try { ws.send(JSON.stringify(msg)); } catch(_) {}
    setTimeout(() => { _suppressLateAudio = false; }, 100);
}

if (SpeechRecognition) {
    recognition = new SpeechRecognition();
    recognition.lang = 'en-US';
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    recognition.onresult = (event) => {
        let interim = '';
        let final = '';
        for (let i = event.resultIndex; i < event.results.length; i++) {
            const res = event.results[i];
            if (res.isFinal) final += res[0].transcript;
            else interim += res[0].transcript;
        }

        // While Jarvis is speaking, only let the WAKE WORD through. Everything
        // else is almost certainly his voice leaking through the speakers and
        // would cause feedback loops. (VAD handles non-wake-word interrupts
        // via mic level, not via the speech recognizer.)
        if (isPlaying) {
            const hay = (interim + ' ' + final).toLowerCase();
            if (WAKE_RE.test(hay) && Date.now() - lastWakeAt > WAKE_DEBOUNCE_MS) {
                lastWakeAt = Date.now();
                enterActiveMode('wake');  // pauses audio via pauseForBargeIn()
            }
            return;
        }

        // --- PASSIVE MODE: scan for wake word, stay quiet otherwise -----
        if (!activeMode) {
            const hay = (interim + ' ' + final).toLowerCase();
            if (WAKE_RE.test(hay) && Date.now() - lastWakeAt > WAKE_DEBOUNCE_MS) {
                lastWakeAt = Date.now();
                enterActiveMode('wake');
                // If the user said "hey jarvis what time is it" in ONE breath,
                // we have the rest of the sentence after the wake phrase in
                // final — submit it instead of waiting for another utterance.
                if (final.trim()) {
                    const rest = stripWakePhrase(final);
                    if (rest) { submitUtterance(rest); return; }
                }
                // Otherwise, stash whatever's after the wake phrase in interim
                // so the 10s window picks it up.
                if (interim) lastInterim = stripWakePhrase(interim);
            }
            // No wake word → stay silent, don't show anything in the HUD.
            return;
        }

        // --- ACTIVE MODE: user is talking to Jarvis ---------------------
        if (interim) {
            const shown = stripWakePhrase(interim);
            liveTranscript.textContent = shown;
            lastInterim = shown;
            // Any new speech extends the 10s window.
            scheduleActiveExpiry();
        }
        if (final.trim()) {
            submitUtterance(final);
        }
    };

    recognition.onend = () => {
        // Continuous mode should never end on its own, but Chrome kills it
        // after ~1min of silence. Restart automatically so the wake word
        // keeps working without the user touching anything.
        recognitionStarted = false;
        liveTranscript.textContent = '';
        if (activeMode && currentState === STATE.LISTENING) {
            setState(STATE.IDLE);
        }
        if (!isPlaying) {
            setTimeout(() => startRecognition(), 250);
        }
    };

    recognition.onerror = (event) => {
        if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
            micStatus.textContent = 'MIC · PERMISSION DENIED';
            hintText.textContent = 'Allow microphone in Chrome';
            recognitionStarted = false;
            return;
        }
        if (event.error === 'audio-capture') {
            micStatus.textContent = 'MIC · NO DEVICE';
            recognitionStarted = false;
            return;
        }
        // no-speech / aborted / network — recover quietly and resume listening
        recognitionStarted = false;
        if (!isPlaying) setTimeout(() => startRecognition(), 400);
    };
} else {
    micStatus.textContent = 'MIC · UNSUPPORTED BROWSER';
    hintText.textContent = 'Use Google Chrome';
}

// Start the continuous recognition engine. Safe to call repeatedly — we
// guard with `recognitionStarted`. The engine runs forever; activation is
// driven by activeMode, not by starting/stopping it.
function startRecognition() {
    if (!recognition || recognitionStarted || isPlaying) return;
    try {
        recognition.start();
        recognitionStarted = true;
        micStatus.textContent = 'MIC · LISTENING FOR "HEY ZERO"';
    } catch (e) {
        // InvalidStateError when already started — fine.
    }
}

function stopRecognition() {
    if (!recognition || !recognitionStarted) return;
    try { recognition.abort(); } catch (_) {}
    recognitionStarted = false;
}

// ========================================================================
//   INPUT — click + spacebar push-to-talk
// ========================================================================
// click only unlocks audio; does NOT start listening
document.addEventListener('click', () => {
    unlockAudio();
});

// Kill every active audio path: queued ElevenLabs MP3s, currently-playing
// <audio> elements, and the browser's speechSynthesis. Called when the user
// interrupts Jarvis by pressing SPACE, and whenever we need a clean slate.
function stopAllAudio() {
    audioQueue = [];
    document.querySelectorAll('audio').forEach(a => {
        try { a.pause(); a.currentTime = 0; } catch(_) {}
    });
    try {
        if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    } catch(_) {}
    isPlaying = false;
}

// SPACE down → interrupt any reply, activate listening.
// SPACE up   → submit whatever was heard (or cancel cleanly if nothing).
// The continuous wake-word engine keeps running underneath — SPACE just
// forces ACTIVE mode so the NEXT utterance is treated as a query.
// Ctrl+Shift+D — fire the staged "monthly report" demo without saying anything.
// Critical for social media recording so we can do retake after retake on the
// same script. Bypasses the wake word + LLM entirely.
document.addEventListener('keydown', (e) => {
    if (!(e.ctrlKey && e.shiftKey && (e.code === 'KeyD' || e.key === 'D' || e.key === 'd'))) return;
    if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) return;
    e.preventDefault();
    unlockAudio();
    try {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'run_demo', id: 'monthly-report' }));
            console.log('[demo] sent run_demo: monthly-report');
        } else {
            console.warn('[demo] WS not open; cannot trigger');
        }
    } catch (err) {
        console.error('[demo] trigger failed:', err);
    }
});

document.addEventListener('keydown', (e) => {
    if (e.code !== 'Space' || e.repeat) return;
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    e.preventDefault();
    pushToTalk = true;
    unlockAudio();
    // BARGE-IN. If Jarvis is speaking, pause (don't kill) his audio. If the
    // user actually says something, submitUtterance() will discard it. If
    // they go silent within ~1.8s, it auto-resumes from where it left off.
    // enterActiveMode itself triggers pauseForBargeIn() when isPlaying.
    startRecognition();
    if (!activeMode) enterActiveMode('space');
});

document.addEventListener('keyup', (e) => {
    if (e.code !== 'Space') return;
    e.preventDefault();
    pushToTalk = false;

    // onresult already sent the final — prevent doubled-reply.
    if (utteranceSent) {
        utteranceSent = false;
        lastInterim = '';
        liveTranscript.textContent = '';
        return;
    }

    // Quick tap: finalize whatever interim we have.
    const pending = (lastInterim || '').trim();
    if (pending && ws && ws.readyState === 1) {
        submitUtterance(pending);
        return;
    }

    // No interim collected — clean cancel, but DON'T abort recognition
    // (we want the wake word to keep working).
    exitActiveMode();
});

// Safety net: if the window loses focus while in ACTIVE mode, drop back
// to PASSIVE. The wake-word engine keeps running in the background.
window.addEventListener('blur', () => {
    pushToTalk = false;
    if (activeMode) exitActiveMode();
});

// ========================================================================
//   TRANSCRIPT
// ========================================================================
function addTranscript(role, text) {
    const div = document.createElement('div');
    // role is 'user' | 'jarvis' | 'brain'; CSS styles each differently
    div.className = role;
    div.textContent = text;
    transcriptBox.appendChild(div);
    transcriptBox.scrollTop = transcriptBox.scrollHeight;
}

// ========================================================================
//   BOOT SEQUENCE — Stark-style power-on. Runs once on page load.
// ========================================================================
const bootOverlay = document.getElementById('boot-overlay');
const bootLog = document.getElementById('boot-log');
const bootProgress = document.getElementById('boot-progress-fill');
const bootGlitch = document.getElementById('boot-glitch');

const BOOT_LINES = [
    { msg: "[0x01] Reactor core...........................", status: "ONLINE",      delay: 800  },
    { msg: "[0x02] Neurolink Brain interface..............", status: "LINKED",      delay: 520  },
    { msg: "[0x03] Persistent memory (Memory.md)...........", status: "LOADED",      delay: 420  },
    { msg: "[0x04] Standing directives.....................", status: "APPLIED",     delay: 420  },
    { msg: "[0x05] Voice synthesis (ElevenLabs)............", status: "ARMED",       delay: 480  },
    { msg: "[0x06] Speech recognition......................", status: "CALIBRATED",  delay: 420  },
    { msg: "[0x07] Vision systems (webcam + screen)........", status: "ARMED",       delay: 520  },
    { msg: "[0x08] Browser automation (Chromium)...........", status: "STANDBY",     delay: 380  },
    { msg: "[0x09] Computer control (mouse/keyboard).......", status: "ARMED",       delay: 380  },
    { msg: "[0x0A] Dev workspace + Claude Code bridge......", status: "READY",       delay: 420  },
    { msg: "[0x0B] Neural network synchronization..........", status: "STABLE",      delay: 520  },
    { msg: "[0x0C] All systems.............................", status: "ONLINE",     delay: 620  },
];

let bootAudio = null;
function bootBeep(freq = 880, duration = 0.05, volume = 0.06, type = "sine") {
    try {
        if (!bootAudio) bootAudio = new (window.AudioContext || window.webkitAudioContext)();
        const osc = bootAudio.createOscillator();
        const gain = bootAudio.createGain();
        osc.type = type;
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0, bootAudio.currentTime);
        gain.gain.linearRampToValueAtTime(volume, bootAudio.currentTime + 0.005);
        gain.gain.exponentialRampToValueAtTime(0.001, bootAudio.currentTime + duration);
        osc.connect(gain);
        gain.connect(bootAudio.destination);
        osc.start();
        osc.stop(bootAudio.currentTime + duration + 0.02);
    } catch (e) {}
}

function addBootLine(line, statusClass = 'ok') {
    const div = document.createElement('div');
    div.className = `boot-line ${statusClass}`;
    div.innerHTML = `<span class="bl-text">${line.msg}</span> <span class="bl-status">${line.status}</span>`;
    bootLog.appendChild(div);
    bootBeep(760 + Math.random() * 240, 0.05, 0.05, "square");
}

async function runBootSequence() {
    // Wait for the intro title/ring animation to settle
    await new Promise(r => setTimeout(r, 1800));

    const total = BOOT_LINES.length;
    for (let i = 0; i < total; i++) {
        addBootLine(BOOT_LINES[i]);
        bootProgress.style.width = `${((i + 1) / total) * 100}%`;
        await new Promise(r => setTimeout(r, BOOT_LINES[i].delay));
    }

    // Final glitch burst
    await new Promise(r => setTimeout(r, 250));
    bootBeep(1200, 0.18, 0.08, "triangle");
    setTimeout(() => bootBeep(240, 0.4, 0.1, "sine"), 120);
    bootGlitch.classList.add('flash');

    // Fade out overlay, reveal the main UI
    await new Promise(r => setTimeout(r, 400));
    bootOverlay.classList.add('fading');
    await new Promise(r => setTimeout(r, 900));
    bootOverlay.classList.add('gone');

    // Audible greeting — have Jarvis actually say hello (via TTS)
    // This runs only after the user has clicked at least once (audio unlock)
    setTimeout(() => {
        if (audioUnlocked && ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ text: "__boot_greet__" }));
        }
    }, 500);
}

// Kick off boot sequence on page load
runBootSequence();

// ========================================================================
//   WEBCAM — Jarvis's eyes. Captures a JPEG frame on every spoken turn.
// ========================================================================
const webcamVideo = document.getElementById('webcam');
const webcamCanvas = document.getElementById('webcam-canvas');
const webcamContainer = document.getElementById('webcam-container');
const webcamCtx = webcamCanvas ? webcamCanvas.getContext('2d') : null;
let webcamReady = false;

async function initWebcam() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({
            video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' },
        });
        webcamVideo.srcObject = stream;
        await webcamVideo.play();
        webcamCanvas.width = 640;
        webcamCanvas.height = 480;
        webcamReady = true;
        webcamContainer.classList.remove('hidden');
        console.log('[jarvis] Webcam initialized');
    } catch (err) {
        console.warn('[jarvis] Webcam not available:', err.message);
        webcamReady = false;
        webcamContainer.classList.add('hidden');
    }
}

function captureWebcamFrame() {
    if (!webcamReady || !webcamCtx) return null;
    try {
        webcamCtx.drawImage(webcamVideo, 0, 0, webcamCanvas.width, webcamCanvas.height);

        // Skip black/dark frames — these happen when the tab is backgrounded,
        // the lid is closed, the lens cap is on, or the room is dark. Without
        // this guard the LLM sees a black image and "responds" with "I can't
        // see you, the screen is black", which is confusing UX. Sample a
        // 16x16 grid; if the average luminance is too low we just don't
        // attach a frame this turn.
        try {
            const sample = webcamCtx.getImageData(
                0, 0,
                Math.min(16, webcamCanvas.width),
                Math.min(16, webcamCanvas.height)
            ).data;
            let lumaSum = 0, n = 0;
            for (let i = 0; i < sample.length; i += 4) {
                lumaSum += 0.299 * sample[i] + 0.587 * sample[i + 1] + 0.114 * sample[i + 2];
                n++;
            }
            const avgLuma = n ? lumaSum / n : 0;
            if (avgLuma < 12) return null;       // 0-255 scale; under 12 = effectively black
        } catch (_) { /* getImageData can throw under some sandboxes — fall through */ }

        const dataUrl = webcamCanvas.toDataURL('image/jpeg', 0.6);
        // Strip the "data:image/jpeg;base64," prefix — server expects raw base64
        return dataUrl.split(',')[1] || null;
    } catch (e) {
        console.warn('[jarvis] Webcam capture error:', e);
        return null;
    }
}

function flashWebcamEye() {
    webcamContainer.classList.add('seeing');
    setTimeout(() => webcamContainer.classList.remove('seeing'), 800);
}

// ========================================================================
//   BOOT
// ========================================================================
setState(STATE.IDLE);
hintText.textContent = 'Click once to enable mic';
initWebcam();
connect();

// Kick the continuous wake-word listener alive as soon as the user makes any
// gesture (required by Chrome's autoplay / mic-activation rules). After that
// it stays running forever, auto-restarting via recognition.onend.
let _listenerArmed = false;
function armWakeListener() {
    if (_listenerArmed) return;
    _listenerArmed = true;
    startRecognition();
    hintText.textContent = 'Say "Hey Zero" or tap SPACE';
}
document.addEventListener('click',    armWakeListener, { once: false });
document.addEventListener('keydown',  armWakeListener, { once: false });
document.addEventListener('touchend', armWakeListener, { once: false });