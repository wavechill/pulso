/* Batch the note heads in one textured GPU draw. The caller retains its 2D
   renderer for software-only browsers and GPUs that lose their context. */
export function createGpuNotes(stage, tiles) {
  const canvas = document.createElement("canvas");
  canvas.className = "rail-gpu";
  canvas.style.pointerEvents = "none";
  canvas.setAttribute("aria-hidden", "true");
  const gl = canvas.getContext("webgl", {
    alpha: true,
    antialias: false,
    depth: false,
    stencil: false,
    premultipliedAlpha: true,
    preserveDrawingBuffer: false,
    powerPreference: "high-performance",
    failIfMajorPerformanceCaveat: true,
  });
  if (!gl) return null;

  const release = gl.getExtension("WEBGL_lose_context");
  const debug = gl.getExtension("WEBGL_debug_renderer_info");
  const device = debug && gl.getParameter(debug.UNMASKED_RENDERER_WEBGL);
  if (
    /swiftshader|llvmpipe|softpipe|software (adapter|rasterizer)|basic render driver/i.test(
      device || "",
    )
  ) {
    release?.loseContext();
    return null;
  }

  const shaders = [];
  let program,
    texture,
    buffer,
    lost = false;
  function onLoss() {
    lost = true;
    canvas.hidden = true;
  }
  function destroy() {
    canvas.removeEventListener("webglcontextlost", onLoss);
    canvas.remove();
    if (!gl.isContextLost()) {
      if (program) gl.deleteProgram(program);
      if (texture) gl.deleteTexture(texture);
      if (buffer) gl.deleteBuffer(buffer);
      for (const shader of shaders) gl.deleteShader(shader);
      release?.loseContext();
    }
    lost = true;
  }
  function shader(type, source) {
    const value = gl.createShader(type);
    shaders.push(value);
    gl.shaderSource(value, source);
    gl.compileShader(value);
    gl.attachShader(program, value);
  }

  try {
    program = gl.createProgram();
    shader(
      gl.VERTEX_SHADER,
      `
      attribute vec4 vertex;
      uniform vec2 scale;
      varying vec2 uv;
      void main() {
        uv = vertex.zw;
        gl_Position = vec4(vertex.xy * scale + vec2(-1.0, 1.0), 0.0, 1.0);
      }
    `,
    );
    shader(
      gl.FRAGMENT_SHADER,
      `
      precision mediump float;
      uniform sampler2D atlas;
      varying vec2 uv;
      void main() { gl_FragColor = texture2D(atlas, uv); }
    `,
    );
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      destroy();
      return null;
    }
    gl.useProgram(program);
    const scale = gl.getUniformLocation(program, "scale");
    const attribute = gl.getAttribLocation(program, "vertex");
    gl.uniform1i(gl.getUniformLocation(program, "atlas"), 0);

    const atlas = document.createElement("canvas");
    const regions = new Map();
    const sources = [...new Set(tiles)];
    // Transparent padding keeps adjacent sprites out of the bilinear samples.
    atlas.width = sources.reduce((width, tile) => width + tile.width + 2, 0);
    atlas.height = Math.max(...sources.map((tile) => tile.height)) + 2;
    const painter = atlas.getContext("2d");
    let offset = 1;
    for (const tile of sources) {
      painter.drawImage(tile, offset, 1);
      regions.set(tile, [
        offset / atlas.width,
        1 / atlas.height,
        (offset + tile.width) / atlas.width,
        (1 + tile.height) / atlas.height,
      ]);
      offset += tile.width + 2;
    }
    texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, atlas);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.clearColor(0, 0, 0, 0);
    buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.enableVertexAttribArray(attribute);
    gl.vertexAttribPointer(attribute, 4, gl.FLOAT, false, 0, 0);

    let data = new Float32Array(64 * 6 * 4);
    let used = 0,
      previousWidth = 0,
      previousHeight = 0;
    gl.bufferData(gl.ARRAY_BUFFER, data.byteLength, gl.DYNAMIC_DRAW);
    if (gl.getError() !== gl.NO_ERROR) {
      destroy();
      return null;
    }

    function vertex(x, y, u, v) {
      data[used++] = x;
      data[used++] = y;
      data[used++] = u;
      data[used++] = v;
    }
    function begin(width, height, dpr) {
      if (lost) return false;
      const pixelsX = Math.max(1, Math.round(width * dpr));
      const pixelsY = Math.max(1, Math.round(height * dpr));
      if (canvas.width !== pixelsX || canvas.height !== pixelsY) {
        canvas.width = pixelsX;
        canvas.height = pixelsY;
        gl.viewport(0, 0, pixelsX, pixelsY);
      }
      if (width !== previousWidth || height !== previousHeight) {
        gl.uniform2f(scale, 2 / width, -2 / height);
        previousWidth = width;
        previousHeight = height;
      }
      used = 0;
      gl.clear(gl.COLOR_BUFFER_BIT);
      return true;
    }
    function sprite(tile, x, y, width, height) {
      const uv = regions.get(tile);
      if (!uv) return;
      if (used + 24 > data.length) {
        const expanded = new Float32Array(data.length * 2);
        expanded.set(data);
        data = expanded;
        gl.bufferData(gl.ARRAY_BUFFER, data.byteLength, gl.DYNAMIC_DRAW);
      }
      const right = x + width,
        bottom = y + height;
      vertex(x, y, uv[0], uv[1]);
      vertex(right, y, uv[2], uv[1]);
      vertex(x, bottom, uv[0], uv[3]);
      vertex(x, bottom, uv[0], uv[3]);
      vertex(right, y, uv[2], uv[1]);
      vertex(right, bottom, uv[2], uv[3]);
    }
    function finish() {
      if (!used || lost) return;
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, data.subarray(0, used));
      gl.drawArrays(gl.TRIANGLES, 0, used / 4);
    }

    canvas.addEventListener("webglcontextlost", onLoss);
    stage.insertBefore(canvas, stage.querySelector("#note-layer"));
    return { begin, sprite, finish, destroy };
  } catch {
    destroy();
    return null;
  }
}
