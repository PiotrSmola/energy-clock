(() => {
  "use strict";

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const locale = document.documentElement.lang || "en";
  const money = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  if (window.htmx) {
    window.htmx.config.globalViewTransitions = !reduceMotion;
    window.htmx.config.scrollBehavior = "auto";
  }

  document.addEventListener("htmx:afterSwap", (event) => {
    const target = event.detail.target;
    if (!target || reduceMotion) {
      return;
    }
    target.classList.add("swap-in");
    window.setTimeout(() => target.classList.remove("swap-in"), 900);
  });

  const THEME_COOKIE = "energy_clock_theme";

  const applyTheme = (value) => {
    const root = document.documentElement;
    root.classList.add("theme-transition");
    root.dataset.theme = value;
    document.cookie = `${THEME_COOKIE}=${value}; max-age=31536000; path=/; samesite=lax`;
    document.querySelectorAll(".theme button").forEach((button) => {
      if (button.value === value) {
        button.setAttribute("aria-current", "true");
      } else {
        button.removeAttribute("aria-current");
      }
    });
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      meta.setAttribute("content", value === "dark" ? "#121316" : "#f4f4f1");
    }
    window.setTimeout(() => root.classList.remove("theme-transition"), 400);
  };

  document.addEventListener("click", (event) => {
    const button = event.target.closest(".theme button");
    if (!button) {
      return;
    }
    event.preventDefault();
    applyTheme(button.value);
  });

  const markSystemTheme = () => {
    const root = document.documentElement;
    if (root.dataset.theme !== "auto") {
      return;
    }
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.querySelectorAll(".theme button").forEach((button) => {
      button.classList.toggle("is-system", button.value === (dark ? "dark" : "light"));
    });
  };

  markSystemTheme();

  const tipFor = (bar) => {
    const figure = bar.closest(".chart");
    return figure ? figure.querySelector(".chart-tip") : null;
  };

  const renderTip = (bar, tip) => {
    const label = bar.closest(".chart").dataset.labelRes || "RES";
    const res = bar.dataset.r ? ` · ${label} ${bar.dataset.r}%` : "";
    tip.innerHTML = `<b>${bar.dataset.t}</b> ${bar.dataset.d}<br>${bar.dataset.p} PLN/MWh${res}`;
    tip.hidden = false;
  };

  const moveTip = (event, tip) => {
    tip.style.left = `${event.clientX}px`;
    tip.style.top = `${event.clientY}px`;
  };

  document.addEventListener("pointerover", (event) => {
    const bar = event.target.closest(".bar");
    if (!bar) {
      return;
    }
    const tip = tipFor(bar);
    if (tip) {
      renderTip(bar, tip);
      moveTip(event, tip);
    }
  });

  document.addEventListener("pointermove", (event) => {
    const bar = event.target.closest(".bar");
    if (!bar) {
      return;
    }
    const tip = tipFor(bar);
    if (tip && !tip.hidden) {
      moveTip(event, tip);
    }
  });

  document.addEventListener("pointerout", (event) => {
    const bar = event.target.closest(".bar");
    if (!bar) {
      return;
    }
    const related = event.relatedTarget;
    if (related && related.closest && related.closest(".bar")) {
      return;
    }
    const tip = tipFor(bar);
    if (tip) {
      tip.hidden = true;
    }
  });

  const tickClocks = () => {
    const now = new Date();
    const angle = (now.getHours() + now.getMinutes() / 60) * 15;
    document.querySelectorAll(".ring-hand").forEach((hand) => {
      hand.style.setProperty("--angle", `${angle.toFixed(1)}deg`);
    });
    document.querySelectorAll(".chart-svg[data-start]").forEach((svg) => {
      const start = Number(svg.dataset.start) * 1000;
      const end = Number(svg.dataset.end) * 1000;
      const left = Number(svg.dataset.left);
      const right = Number(svg.dataset.right);
      const marker = svg.querySelector(".now-marker");
      if (!marker || !(start < end)) {
        return;
      }
      const moment = now.getTime();
      if (moment < start || moment > end) {
        return;
      }
      const x = (left + ((moment - start) / (end - start)) * (right - left)).toFixed(2);
      marker.querySelectorAll("line").forEach((line) => {
        line.setAttribute("x1", x);
        line.setAttribute("x2", x);
      });
      marker.querySelectorAll("circle").forEach((dot) => dot.setAttribute("cx", x));
      marker.querySelectorAll("text").forEach((text) => text.setAttribute("x", x));
    });
  };

  window.setInterval(tickClocks, 60000);

  const readPresets = () => {
    const node = document.getElementById("appliance-presets");
    if (!node) {
      return {};
    }
    try {
      return JSON.parse(node.textContent);
    } catch {
      return {};
    }
  };

  const durationText = (minutes) => {
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    if (hours && rest) {
      return `${hours} h ${rest} min`;
    }
    if (hours) {
      return `${hours} h`;
    }
    return `${rest} min`;
  };

  document.addEventListener("alpine:init", () => {
    window.Alpine.data("plannerForm", () => ({
      presets: {},
      preset: "",
      duration: 120,
      power: 2,
      nowPrice: null,
      init() {
        this.presets = readPresets();
        const form = this.$el;
        const price = parseFloat(String(form.dataset.nowPrice || "").replace(",", "."));
        this.nowPrice = Number.isFinite(price) ? price : null;
        this.preset = form.elements.appliance.value;
        this.duration = Number(form.elements.duration_minutes.value) || 120;
        this.power = parseFloat(String(form.elements.power_kw.value).replace(",", ".")) || 2;
        this.applyPreset();
      },
      applyPreset() {
        const preset = this.presets[this.preset];
        if (preset) {
          this.duration = Number(preset.duration_minutes);
          this.power = parseFloat(preset.power_kw);
        }
      },
      get locked() {
        return Boolean(this.presets[this.preset]);
      },
      get energy() {
        const value = (Number(this.power) * Number(this.duration)) / 60;
        return Number.isFinite(value) ? value : 0;
      },
      get energyLabel() {
        return money.format(this.energy);
      },
      get durationLabel() {
        return Number.isFinite(Number(this.duration)) ? durationText(Number(this.duration)) : "";
      },
      get powerLabel() {
        return Number.isFinite(Number(this.power)) ? `${money.format(Number(this.power))} kW` : "";
      },
      get nowCost() {
        if (this.nowPrice === null) {
          return null;
        }
        return money.format((this.nowPrice * this.energy) / 1000);
      },
    }));
  });
})();
