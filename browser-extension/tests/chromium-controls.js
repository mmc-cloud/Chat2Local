/* Native Chromium fixture. Runs real packaged code without CDP/Playwright. */
(async () => {
  const records = [], fixture = document.querySelector("#fixture"), portals = document.querySelector("#portals");
  const assert = (value, message) => { if (!value) throw Error(message); };
  const call = (operation, args = {}) => new Promise((resolve) => window.domListener({ type: "chat2local.dom", operation,
    arguments: { tab: { tab_id: 1 }, ...args }, expected_url: location.href, expires_at: Date.now() + 10000 }, { id: "controlled-test" }, resolve));
  const ok = async (operation, args = {}) => {
    const response = await call(operation, args);
    assert(response.ok, `${operation}: ${JSON.stringify(response)}`);
    return response.result;
  };
  const inspect = async (id, extra = {}) => (await ok("inspect", { target: { id }, ...extra })).elements[0];
  const ready = (target, extra = {}) => call("wait_for", { target, state: "actionable", timeout_ms: 0, ...extra });
  const reset = (html) => { fixture.innerHTML = html; portals.innerHTML = ""; window.scrollTo(0, 0); };
  const check = async (name, run) => {
    try { await run(); records.push({ name, passed: true }); }
    catch (error) { records.push({ name, passed: false, error: String(error.stack || error) }); }
    await fetch("/progress", { method: "POST", body: JSON.stringify(records) });
  };
  try {
    await check("set_checked accepts reached native/ARIA state after hiding, disabling or readonly", async () => {
      for (const kind of ["native", "checkbox", "switch"]) for (const change of ["hide", "disable", "readonly"]) {
        reset(kind === "native" ? '<input id="x" type="checkbox">' : `<button id="x" role="${kind}" aria-checked="false">Check</button>`);
        const element = document.querySelector("#x");
        let clicks = 0;
        element.addEventListener("click", () => {
          clicks++;
          if (kind !== "native") element.setAttribute("aria-checked", "true");
          if (change === "hide") element.hidden = true;
          if (change === "disable") { element.disabled = true; element.setAttribute("aria-disabled", "true"); }
          if (change === "readonly") { element.readOnly = true; element.setAttribute("aria-readonly", "true"); }
        });
        const result = await ok("set_checked", { target: { id: "x" }, checked: true });
        assert(result.checked && result.changed && clicks === 1, `${kind}/${change}`);
        assert(kind === "native" ? element.checked : element.getAttribute("aria-checked") === "true", "actual final state");
      }
    });
    await check("actionable self and descendant hit is read-only", async () => {
      reset('<button id="x"><span style="display:block;pointer-events:auto">Run</span></button>');
      const element = document.querySelector("#x"), beforeFocus = document.activeElement, beforeScroll = window.scrollY;
      let effects = 0;
      for (const event of ["click", "focus", "input", "change"]) element.addEventListener(event, () => effects++);
      const box = element.getBoundingClientRect();
      assert(document.elementFromPoint(box.left + 10, box.top + 10) === element.firstElementChild, "actual descendant hit");
      assert((await ready({ id: "x" })).ok, "child should qualify");
      assert(effects === 0 && document.activeElement === beforeFocus && window.scrollY === beforeScroll, "no side effects");
    });
    await check("actionable full occlusion rejected; covered center with reachable edges accepted", async () => {
      reset('<button id="x" style="position:absolute;left:20px;top:20px;width:200px;height:80px">Run</button>'
        + '<div id="cover" style="position:absolute;z-index:5;left:20px;top:20px;width:200px;height:80px"></div>');
      assert((await ready({ id: "x" })).error?.code === "wait_timeout", "full cover");
      document.querySelector("#cover").style.cssText = "position:absolute;z-index:5;left:80px;top:20px;width:80px;height:80px";
      assert((await ready({ id: "x" })).ok, "center-only cover should allow edge");
    });
    await check("pointer-events, hidden, inert, zero geometry and offscreen do not qualify", async () => {
      for (const style of ["pointer-events:none", "display:none", "visibility:hidden", "opacity:0",
        "min-width:0;min-height:0;width:0;height:0;padding:0;border:0", "position:absolute;left:2000px;top:20px"]) {
        reset(`<button id="x" style="${style}">Run</button>`);
        assert((await ready({ id: "x" })).error?.code === "wait_timeout", style);
      }
      reset('<button id="x" inert>Run</button>');
      assert((await ready({ id: "x" })).error?.code === "wait_timeout", "inert");
      assert(window.scrollX === 0 && window.scrollY === 0, "wait must not scroll offscreen target");
    });
    await check("partially offscreen and multi-line inline target has a reachable viewport fragment", async () => {
      reset('<button id="x" style="position:absolute;left:-150px;top:-20px;width:200px;height:80px">Run</button>');
      assert((await ready({ id: "x" })).ok, "partially visible target");
      reset('<div style="width:70px"><span id="x" role="button">Alpha beta gamma delta epsilon</span></div>');
      assert(document.querySelector("#x").getClientRects().length > 1, "real inline fragments");
      assert((await ready({ id: "x" })).ok, "inline fragments");
    });
    await check("overflow clipping and dynamic overlay removal use actual hit testing", async () => {
      reset('<div style="width:100px;height:30px;overflow:clip"><button id="x" style="transform:translateY(80px)">Clipped</button></div>');
      assert((await ready({ id: "x" })).error?.code === "wait_timeout", "clipped child not hittable");
      reset('<button id="x">Run</button><div id="cover" style="position:fixed;inset:0;z-index:5"></div>');
      setTimeout(() => document.querySelector("#cover").remove(), 40);
      assert((await ready({ id: "x" }, { timeout_ms: 500 })).ok, "overlay disappears within wait");
    });
    await check("scroll discovery matches native hidden/clip/auto/scroll and boundaries", async () => {
      for (const overflow of ["hidden", "clip", "auto", "scroll"]) {
        reset(`<div id="x" style="width:120px;height:80px;overflow:${overflow}"><div style="width:500px;height:600px"></div></div>`);
        const initial = await inspect("x");
        assert(initial.scroll.x.scrollable === (overflow !== "clip") && initial.scroll.y.at_start, overflow);
        const response = await call("scroll", { container: "element", target: { ref: initial.ref }, delta_x: 10000, delta_y: 10000 });
        if (overflow === "clip") assert(response.error?.code === "target_not_scrollable", "clip refused");
        else {
          assert(response.ok, JSON.stringify(response));
          const final = await inspect("x");
          assert(final.scroll.x.at_end && final.scroll.y.at_end && !final.scroll.y.at_start, "actual edge state");
          assert(final.scroll.y.position === final.scroll.y.max, "actual native range");
        }
      }
    });
    await check("RTL and reverse flex report native negative ranges without changing coordinates", async () => {
      for (const style of ["direction:rtl", "display:flex;flex-direction:row-reverse"]) {
        reset(`<div id="x" style="width:120px;height:80px;overflow:auto;${style}"><div style="min-width:500px;height:50px"></div></div>`);
        const element = document.querySelector("#x"); element.scrollLeft = -10000;
        const state = (await inspect("x")).scroll.x;
        assert(state.position < 0 && state.min < 0 && state.max === 0 && state.at_end, JSON.stringify(state));
      }
      reset('<div id="x" style="display:flex;flex-direction:column-reverse;width:120px;height:80px;overflow:auto"><div style="min-height:600px">Content</div></div>');
      document.querySelector("#x").scrollTop = -10000;
      const state = (await inspect("x")).scroll.y;
      assert(state.position < 0 && state.min < 0 && state.at_end, JSON.stringify(state));
    });
    await check("page scroll observation agrees with root overflow and actual programmatic scroll", async () => {
      try {
        for (const overflow of ["visible", "hidden", "clip"]) {
          reset('<div style="height:1800px;width:120px">Tall</div>');
          document.documentElement.style.overflow = overflow;
          const before = (await ok("inspect", { target: { css: ":root" } })).elements[0].scroll;
          await ok("scroll", { container: "page", delta_y: 10000 });
          const after = (await ok("inspect", { target: { css: ":root" } })).elements[0].scroll;
          assert(before.is_page && before.y.scrollable === (after.y.position > 0), `${overflow}: ${JSON.stringify(after)}`);
          assert(after.y.position === after.y.max && after.y.at_end, "actual page range");
        }
      } finally { document.documentElement.style.overflow = ""; window.scrollTo(0, 0); }
    });
    await check("vertical/sideways writing and reverse flex wrap match measured native coordinate ranges", async () => {
      for (const style of ["writing-mode:vertical-rl", "writing-mode:vertical-lr;direction:rtl", "writing-mode:sideways-lr",
        "writing-mode:sideways-lr;direction:rtl", "display:flex;flex-wrap:wrap-reverse",
        "display:flex;flex-direction:column;flex-wrap:wrap-reverse", "display:flex;flex-direction:row-reverse;flex-wrap:wrap-reverse",
        "writing-mode:vertical-rl;display:flex;flex-direction:column-reverse;flex-wrap:wrap-reverse"]) {
        reset(`<div id="x" style="width:120px;height:80px;overflow:auto;${style}"><div style="min-width:300px;min-height:300px">One</div><div style="min-width:300px;min-height:300px">Two</div></div>`);
        const element = document.querySelector("#x");
        element.scrollLeft = -10000; element.scrollTop = -10000;
        const minX = element.scrollLeft, minY = element.scrollTop;
        element.scrollLeft = 10000; element.scrollTop = 10000;
        const maxX = element.scrollLeft, maxY = element.scrollTop;
        const scroll = (await inspect("x")).scroll;
        assert(scroll.x.min === minX && scroll.x.max === maxX && scroll.y.min === minY && scroll.y.max === maxY,
          `${style}: ${JSON.stringify({ scroll, minX, maxX, minY, maxY })}`);
      }
    });
    await check("native options page and tri-state input + hidden visible label verify actual state", async () => {
      reset('<select id="select">' + Array.from({ length: 62 }, (_, i) => `<option value="v${i}">Choice ${i}</option>`).join("")
        + '</select><input id="x" type="checkbox" hidden><label for="x">Consent</label>');
      let offset = 0, count = 0;
      do {
        const page = (await inspect("select", { select_options: { offset, limit: 17 } })).select_options;
        count += page.options.length; offset = page.next_offset;
      } while (offset !== null);
      assert(count === 62, "all choices reachable");
      await ok("select", { target: { id: "select" }, options: [{ index: 61 }] });
      assert((await inspect("select")).state.selection.selected_indices[0] === 61, "selection observed");
      const input = document.querySelector("#x"); input.indeterminate = true;
      assert((await inspect("x")).state.check_state === "mixed", "native mixed observed");
      await ok("set_checked", { target: { id: "x" }, checked: true });
      assert(input.checked && !input.indeterminate, "label activates actual hidden input");
      assert((await ok("set_checked", { target: { id: "x" }, checked: true })).changed === false, "idempotent");
      input.hidden = false; input.style.opacity = "0";
      await ok("set_checked", { target: { id: "x" }, checked: false });
      assert(!input.checked, "CSS opacity-hidden input uses associated visible label");
    });
    for (const framework of ["react", "vue"]) {
      await check(`${framework} actual portal/teleport combobox six-step workflow`, async () => {
        reset('<div id="app"></div>');
        let cleanup;
        if (framework === "react") {
          const { React: R, ReactDOM: D } = window;
          function Combo() {
            const [open, setOpen] = R.useState(false), [selected, setSelected] = R.useState("");
            return R.createElement(R.Fragment, null,
              R.createElement("button", { id: "combo", role: "combobox", "aria-expanded": open, "aria-controls": "choices",
                "aria-owns": "choices", "aria-activedescendant": selected ? "pear" : undefined, onClick: () => setOpen(true) }, selected || "Choose"),
              open && D.createPortal(R.createElement("div", { id: "choices", role: "listbox" },
                R.createElement("div", { id: "pear", role: "option", "aria-selected": selected === "Pear", onClick: () => { setSelected("Pear"); setOpen(false); } }, "Pear")), portals));
          }
          const root = D.createRoot(document.querySelector("#app")); D.flushSync(() => root.render(R.createElement(Combo))); cleanup = () => root.unmount();
        } else {
          const app = Vue.createApp({ data: () => ({ open: false, selected: "" }), template: '<button id="combo" role="combobox" :aria-expanded="open" aria-controls="choices" aria-owns="choices" :aria-activedescendant="selected?\'pear\':undefined" @click="open=true">{{selected||"Choose"}}</button>'
            + '<teleport to="#portals"><div v-if="open" id="choices" role="listbox"><div id="pear" role="option" :aria-selected="selected===\'Pear\'" @click="selected=\'Pear\';open=false">Pear</div></div></teleport>' });
          app.mount(document.querySelector("#app")); cleanup = () => app.unmount();
        }
        try {
          const before = await inspect("combo"); assert(before.role === "combobox" && before.state.controls === "choices", "discovered");
          await ok("click", { target: { ref: before.ref } });
          await ok("wait_for", { target: { role: "option", name: "Pear" }, state: "visible", timeout_ms: 500 });
          assert(!document.querySelector("#app").contains(document.querySelector("#choices")), "real external portal");
          const options = (await ok("dom.inspect", { css: '[role="option"]', scope: "#choices", depth: 0 })).elements;
          assert(options[0].state.selected === false, "option selection readable");
          await ok("click", { target: { ref: options[0].ref } });
          await Vue.nextTick(); await new Promise((resolve) => setTimeout(resolve, 0));
          const after = (await ok("inspect", { target: { ref: before.ref } })).elements[0];
          assert(after.state.expanded === false && after.state.active_descendant === "pear" && after.name === "Pear", "selection verified through original combo ref");
          assert((await call("wait_for", { target: { ref: options[0].ref }, state: "detached", timeout_ms: 0 })).error?.code === "stale_target", "removed option ref remains stale, not absence");
        } finally { cleanup(); }
      });
      await check(`${framework} ARIA switch and hidden checkbox label update component models`, async () => {
        reset('<div id="app"></div>');
        let cleanup;
        if (framework === "react") {
          const { React: R, ReactDOM: D } = window;
          function Settings() {
            const [on, setOn] = R.useState(false), [native, setNative] = R.useState(false);
            return R.createElement(R.Fragment, null,
              R.createElement("button", { id: "switch", role: "switch", "aria-checked": on, onClick: () => setOn(!on) }, "Power"),
              R.createElement("input", { id: "native", type: "checkbox", hidden: true, checked: native, onChange: (event) => setNative(event.target.checked) }),
              R.createElement("label", { htmlFor: "native" }, "Consent"), R.createElement("output", { id: "model" }, `${on}:${native}`));
          }
          const root = D.createRoot(document.querySelector("#app")); D.flushSync(() => root.render(R.createElement(Settings))); cleanup = () => root.unmount();
        } else {
          const app = Vue.createApp({ data: () => ({ on: false, native: false }), template: '<button id="switch" role="switch" :aria-checked="on" @click="on=!on">Power</button><input id="native" type="checkbox" hidden v-model="native"><label for="native">Consent</label><output id="model">{{on}}:{{native}}</output>' });
          app.mount(document.querySelector("#app")); cleanup = () => app.unmount();
        }
        try {
          for (const id of ["switch", "native"]) {
            await ok("set_checked", { target: { id }, checked: true });
            assert((await ok("set_checked", { target: { id }, checked: true })).changed === false, "repeat is noop");
            assert((await inspect(id)).state.checked, "observed state");
          }
          assert(document.querySelector("#model").textContent === "true:true", "real framework model");
        } finally { cleanup(); }
      });
    }
  } catch (error) { records.push({ name: "fixture harness", passed: false, error: String(error.stack || error) }); }
  const report = { user_agent: navigator.userAgent,
    passed: records.filter((record) => record.passed).length, failed: records.filter((record) => !record.passed).length, checks: records };
  document.querySelector("#test-report").textContent = JSON.stringify(report);
  await fetch("/report", { method: "POST", body: JSON.stringify(report) });
})();
