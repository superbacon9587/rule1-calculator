(() => {
  "use strict";

  const form = document.getElementById("search-form");
  const input = document.getElementById("ticker-input");
  const statusArea = document.getElementById("status-area");
  const results = document.getElementById("results");
  const quickPicks = document.getElementById("quick-picks");

  const BIGFIVE_ORDER = ["roic", "sales", "eps", "equity", "fcf"];

  const els = {
    name: document.getElementById("company-name"),
    ticker: document.getElementById("company-ticker"),
    sector: document.getElementById("company-sector"),
    price: document.getElementById("current-price"),
    sticker: document.getElementById("sticker-price"),
    mos: document.getElementById("mos-price"),
    verdict: document.getElementById("verdict-pill"),
    warningBanner: document.getElementById("warning-banner"),
    bigFiveGrid: document.getElementById("big-five-grid"),
    bigfiveChart: document.getElementById("bigfive-chart"),
    bigfivePlaceholder: document.getElementById("bigfive-chart-placeholder"),
    moatRatio: document.getElementById("moat-ratio"),
    moatImage: document.getElementById("moat-image"),
    growthChart: document.getElementById("growth-chart"),
    growthPlaceholder: document.getElementById("growth-chart-placeholder"),
    debtChart: document.getElementById("debt-chart"),
    debtPlaceholder: document.getElementById("debt-chart-placeholder"),
    stickerTable: document.getElementById("sticker-table"),
    sourcesList: document.getElementById("sources-list"),
    leadershipList: document.getElementById("leadership-list"),
    leadershipPlaceholder: document.getElementById("leadership-placeholder"),
  };

  let currentData = null;
  let activeMetric = null;

  function fmtMoney(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return "n/a";
    return "$" + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function fmtPct(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return "n/a";
    return (v * 100).toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + "%";
  }

  function setLoading(ticker) {
    statusArea.innerHTML = `<div class="status-loading">Fetching live filings for <strong>${escapeHtml(ticker)}</strong>&hellip;</div>`;
  }

  function setError(message) {
    statusArea.innerHTML = `<div class="status-error">${escapeHtml(message)}</div>`;
  }

  function clearStatus() {
    statusArea.innerHTML = "";
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = String(str);
    return div.innerHTML;
  }

  function verdictClass(verdict) {
    if (!verdict) return "";
    const v = verdict.toLowerCase();
    if (v.includes("sale") || v.includes("below")) return "verdict-good";
    if (v.includes("above sticker") || v.includes("overpriced") || v.includes("expensive")) return "verdict-critical";
    return "verdict-warning";
  }

  function showImageOrPlaceholder(imgEl, placeholderEl, src, offline, onSettled) {
    if (offline) {
      imgEl.hidden = true;
      placeholderEl.hidden = false;
      imgEl.removeAttribute("src");
      if (onSettled) onSettled();
      return;
    }
    imgEl.hidden = false;
    placeholderEl.hidden = true;
    imgEl.onerror = () => {
      imgEl.hidden = true;
      placeholderEl.hidden = false;
      if (onSettled) onSettled();
    };
    imgEl.onload = () => {
      if (onSettled) onSettled();
    };
    imgEl.src = src;
  }

  function selectBigFiveMetric(key) {
    if (!currentData) return;
    activeMetric = key;
    els.bigFiveGrid.querySelectorAll(".stat-tile").forEach((tile) => {
      tile.classList.toggle("is-active", tile.dataset.metric === key);
    });
    const bust = Date.now();
    showImageOrPlaceholder(
      els.bigfiveChart,
      els.bigfivePlaceholder,
      `/api/chart/bigfive/${encodeURIComponent(currentData.ticker)}/${key}.png?_=${bust}`,
      currentData.offline_fallback
    );
  }

  function renderBigFive(bigFive) {
    els.bigFiveGrid.innerHTML = "";
    BIGFIVE_ORDER.forEach((key) => {
      const m = bigFive[key];
      if (!m) return;
      const tone = m.green === true ? "is-green" : m.green === false ? "is-red" : "";
      const windowLabel = m.longest_window ? `${m.longest_window}yr trailing` : "no data";
      const tile = document.createElement("button");
      tile.type = "button";
      tile.className = `stat-tile ${tone}`;
      tile.dataset.metric = key;
      tile.innerHTML = `
        <span class="stat-name">${escapeHtml(m.label)}</span>
        <span class="stat-value ${tone}">${fmtPct(m.longest_value)}</span>
        <span class="stat-window">${escapeHtml(windowLabel)}</span>
      `;
      tile.addEventListener("click", () => selectBigFiveMetric(key));
      els.bigFiveGrid.appendChild(tile);
    });
  }

  function wikipediaUrl(name) {
    // "go=Go" jumps straight to the article on an exact/near-exact title
    // match, and otherwise lands on Wikipedia's own search results for the
    // name -- so this never points at a dead link, even when we can't be
    // sure the person has an article under this exact title.
    return `https://en.wikipedia.org/wiki/Special:Search?search=${encodeURIComponent(name)}&go=Go`;
  }

  // Measures the Sticker Price card (right column) against the Debt payback
  // card (top of the left column) and constrains the Leadership card to
  // whatever vertical room is left, so the two columns' bottoms line up
  // exactly. If the officer list doesn't fit at the default size, the whole
  // list -- text and spacing together, since they're set in em off one
  // font-size -- is scaled down in small steps until it does, down to a
  // floor past which it scrolls instead of shrinking further.
  function fitLeadershipCard() {
    const stickerCard = document.querySelector(".sticker-card");
    const debtCard = document.querySelector(".debt-card");
    const leadershipCard = document.querySelector(".leadership-card");
    const stackedCol = document.querySelector(".stacked-col");
    if (!stickerCard || !debtCard || !leadershipCard || !stackedCol) return;

    // Clear any prior constraint so heights below reflect natural content.
    leadershipCard.style.flex = "";
    leadershipCard.style.height = "";
    els.leadershipList.style.fontSize = "";
    els.leadershipList.style.justifyContent = "";

    const gapStr = getComputedStyle(stackedCol).rowGap || getComputedStyle(stackedCol).gap || "0";
    const gap = parseFloat(gapStr) || 0;
    const available = stickerCard.getBoundingClientRect().height
      - debtCard.getBoundingClientRect().height - gap;
    if (!(available > 0)) return; // degenerate layout (e.g. very narrow window) -- leave natural

    leadershipCard.style.flex = `0 0 ${available}px`;
    leadershipCard.style.height = `${available}px`;

    if (els.leadershipList.hidden) return; // placeholder text already centers itself

    const MAX_FONT = 0.84, MIN_FONT = 0.6, STEP = 0.02;
    let fontSize = MAX_FONT;
    els.leadershipList.style.overflowY = "hidden";
    for (; fontSize >= MIN_FONT; fontSize -= STEP) {
      els.leadershipList.style.fontSize = fontSize.toFixed(2) + "rem";
      if (els.leadershipList.scrollHeight <= els.leadershipList.clientHeight + 1) break;
    }
    // Still doesn't fit at the smallest readable size (an unusually long
    // officer roster) -- keep the floor size and let it scroll internally
    // rather than shrink text past legibility. Switch off center-alignment
    // in that case: a centered flex column clips overflow from BOTH ends,
    // which can hide the first item(s) with no way to scroll back up to
    // them -- top-aligned keeps everything reachable by scrolling down.
    const stillOverflowing = els.leadershipList.scrollHeight > els.leadershipList.clientHeight + 1;
    els.leadershipList.style.overflowY = stillOverflowing ? "auto" : "hidden";
    els.leadershipList.style.justifyContent = stillOverflowing ? "flex-start" : "center";
  }

  function renderLeadership(leadership, offline) {
    const people = (leadership || []).filter((p) => p && p.name);
    if (offline || !people.length) {
      els.leadershipList.innerHTML = "";
      els.leadershipList.hidden = true;
      els.leadershipPlaceholder.hidden = false;
      els.leadershipPlaceholder.textContent = offline
        ? "Leadership names aren't available in cached/offline mode."
        : "No officer data was reported for this ticker.";
      return;
    }
    els.leadershipList.hidden = false;
    els.leadershipPlaceholder.hidden = true;
    els.leadershipList.innerHTML = people.map((p) => `
      <li class="leadership-item">
        <a class="leadership-name" href="${escapeHtml(wikipediaUrl(p.name))}" target="_blank" rel="noopener">${escapeHtml(p.name)}</a>
        ${p.title ? `<span class="leadership-title">${escapeHtml(p.title)}</span>` : ""}
      </li>
    `).join("");
  }

  function renderStickerTable(sticker) {
    const rows = [
      ["Current EPS", fmtMoney(sticker.current_eps)],
      ["Growth rate used", `${fmtPct(sticker.growth_rate)} (${sticker.growth_rate_source || "n/a"})`],
      ["Default PE (2× growth)", sticker.default_pe != null ? sticker.default_pe.toFixed(1) : "n/a"],
      ["Historical avg PE", sticker.historical_pe != null ? sticker.historical_pe.toFixed(1) : "n/a"],
      ["Rule #1 PE used", sticker.rule1_pe != null ? sticker.rule1_pe.toFixed(1) : "n/a"],
      ["Future EPS (10yr)", fmtMoney(sticker.future_eps)],
      ["Future price (10yr)", fmtMoney(sticker.future_price)],
    ];
    let html = rows.map(([label, value]) =>
      `<tr><td class="label">${escapeHtml(label)}</td><td class="value">${value}</td></tr>`
    ).join("");
    html += `<tr class="total"><td class="label">Sticker Price</td><td class="value">${fmtMoney(sticker.sticker_price)}</td></tr>`;
    html += `<tr class="mos"><td class="label">Margin-of-Safety price</td><td class="value">${fmtMoney(sticker.mos_price)}</td></tr>`;
    els.stickerTable.innerHTML = html;
  }

  function render(data) {
    currentData = data;

    els.name.textContent = data.name || data.ticker;
    els.ticker.textContent = data.ticker;
    els.sector.textContent = [data.sector, data.industry].filter(Boolean).join(" — ");
    els.price.textContent = fmtMoney(data.current_price);
    els.sticker.textContent = fmtMoney(data.sticker.sticker_price);
    els.mos.textContent = fmtMoney(data.sticker.mos_price);

    els.verdict.textContent = data.sticker.verdict || "No verdict available.";
    els.verdict.className = `verdict-pill ${verdictClass(data.sticker.verdict)}`;

    if (data.warnings && data.warnings.length) {
      els.warningBanner.hidden = false;
      els.warningBanner.textContent = data.warnings.join(" ");
    } else {
      els.warningBanner.hidden = true;
      els.warningBanner.textContent = "";
    }

    renderBigFive(data.big_five);
    selectBigFiveMetric(activeMetric && data.big_five[activeMetric] ? activeMetric : "sales");

    els.moatRatio.textContent = `${data.moat.green_count} / ${data.moat.total}`;
    els.moatImage.src = `/api/moat/${data.moat.level}.png`;
    els.moatImage.alt = `Castle graphic showing ${data.moat.green_count} of ${data.moat.total} Big Five numbers green`;

    const bust = Date.now();
    showImageOrPlaceholder(els.growthChart, els.growthPlaceholder,
      `/api/chart/growth/${encodeURIComponent(data.ticker)}.png?_=${bust}`, data.offline_fallback);
    // The debt-payback card sits above Leadership in the same column, so its
    // final height (image vs. placeholder, which differ) has to be settled
    // before we measure it below -- re-run the fit once it is, in case the
    // image request resolves after our own immediate measurement.
    showImageOrPlaceholder(els.debtChart, els.debtPlaceholder,
      `/api/chart/debt/${encodeURIComponent(data.ticker)}.png?_=${bust}`, data.offline_fallback,
      fitLeadershipCard);

    renderStickerTable(data.sticker);
    renderLeadership(data.leadership, data.offline_fallback);
    // Run after layout settles from the DOM updates above, so the sticker
    // table and debt card have their final heights before we measure them.
    requestAnimationFrame(fitLeadershipCard);

    els.sourcesList.innerHTML = (data.sources || []).map((url) =>
      `<li><a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(url)}</a></li>`
    ).join("") || "<li>No source links available.</li>";

    results.hidden = false;
  }

  async function runSearch(rawTicker) {
    const ticker = (rawTicker || "").trim().toUpperCase();
    if (!ticker) {
      setError("Type a ticker symbol first.");
      return;
    }
    input.value = ticker;
    setLoading(ticker);
    form.querySelector("button").disabled = true;

    try {
      const resp = await fetch(`/api/analyze?ticker=${encodeURIComponent(ticker)}`);
      const data = await resp.json();
      if (!resp.ok || data.error) {
        throw new Error(data.error || `Request failed (${resp.status})`);
      }
      clearStatus();
      render(data);
      history.replaceState(null, "", `?ticker=${encodeURIComponent(ticker)}`);
    } catch (err) {
      results.hidden = true;
      setError(err.message || "Something went wrong fetching that ticker.");
    } finally {
      form.querySelector("button").disabled = false;
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    runSearch(input.value);
  });

  quickPicks.addEventListener("click", (e) => {
    const btn = e.target.closest(".chip");
    if (!btn) return;
    runSearch(btn.dataset.ticker);
  });

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(fitLeadershipCard, 120);
  });

  // Auto-load a ticker from the URL (?ticker=AAPL) or default to AAPL on first open.
  const params = new URLSearchParams(window.location.search);
  const initial = params.get("ticker") || "AAPL";
  runSearch(initial);
})();
