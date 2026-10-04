(() => {
  "use strict";

  const form = document.getElementById("search-form");
  const input = document.getElementById("ticker-input");
  const statusArea = document.getElementById("status-area");
  const results = document.getElementById("results");
  const quickPicks = document.getElementById("quick-picks");

  // The only tickers rule1.db covers (rendered into the form by the server).
  const SUPPORTED_TICKERS = form && form.dataset.tickers ? form.dataset.tickers.split(",") : [];

  const BIGFIVE_ORDER = ["roic", "sales", "eps", "equity", "fcf"];

  const els = {
    name: document.getElementById("company-name"),
    ticker: document.getElementById("company-ticker"),
    sector: document.getElementById("company-sector"),
    liveLinks: document.getElementById("live-links"),
    price: document.getElementById("current-price"),
    priceLabel: document.getElementById("price-label"),
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
    statusArea.innerHTML = `<div class="status-loading">Loading <strong>${escapeHtml(ticker)}</strong>&hellip;</div>`;
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

  function showImageOrPlaceholder(imgEl, placeholderEl, src, onSettled) {
    if (!imgEl || !placeholderEl) return;
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
    if (!currentData || !els.bigFiveGrid) return;
    activeMetric = key;
    els.bigFiveGrid.querySelectorAll(".stat-tile").forEach((tile) => {
      tile.classList.toggle("is-active", tile.dataset.metric === key);
    });
    const bust = Date.now();
    showImageOrPlaceholder(
      els.bigfiveChart,
      els.bigfivePlaceholder,
      `/api/chart/bigfive/${encodeURIComponent(currentData.ticker)}/${key}.png?_=${bust}`
    );
  }

  function renderBigFive(bigFive) {
    if (!els.bigFiveGrid || !bigFive) return;
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
    return `https://en.wikipedia.org/wiki/Special:Search?search=${encodeURIComponent(name)}&go=Go`;
  }

  function fitLeadershipCard() {
    const stickerCard = document.querySelector(".sticker-card");
    const debtCard = document.querySelector(".debt-card");
    const leadershipCard = document.querySelector(".leadership-card");
    const stackedCol = document.querySelector(".stacked-col");
    if (!stickerCard || !debtCard || !leadershipCard || !stackedCol || !els.leadershipList) return;

    leadershipCard.style.flex = "";
    leadershipCard.style.height = "";
    els.leadershipList.style.fontSize = "";
    els.leadershipList.style.justifyContent = "";

    const gapStr = getComputedStyle(stackedCol).rowGap || getComputedStyle(stackedCol).gap || "0";
    const gap = parseFloat(gapStr) || 0;
    const available = stickerCard.getBoundingClientRect().height
      - debtCard.getBoundingClientRect().height - gap;
    if (!(available > 0)) return;

    leadershipCard.style.flex = `0 0 ${available}px`;
    leadershipCard.style.height = `${available}px`;

    if (els.leadershipList.hidden) return;

    const MAX_FONT = 0.84, MIN_FONT = 0.6, STEP = 0.02;
    let fontSize = MAX_FONT;
    els.leadershipList.style.overflowY = "hidden";
    for (; fontSize >= MIN_FONT; fontSize -= STEP) {
      els.leadershipList.style.fontSize = fontSize.toFixed(2) + "rem";
      if (els.leadershipList.scrollHeight <= els.leadershipList.clientHeight + 1) break;
    }
    const stillOverflowing = els.leadershipList.scrollHeight > els.leadershipList.clientHeight + 1;
    els.leadershipList.style.overflowY = stillOverflowing ? "auto" : "hidden";
    els.leadershipList.style.justifyContent = stillOverflowing ? "flex-start" : "center";
  }

  function renderLeadership(leadership) {
    if (!els.leadershipList || !els.leadershipPlaceholder) return;
    const people = (leadership || []).filter((p) => p && p.name);
    if (!people.length) {
      els.leadershipList.innerHTML = "";
      els.leadershipList.hidden = true;
      els.leadershipPlaceholder.hidden = false;
      els.leadershipPlaceholder.textContent = "No officer data was reported for this ticker.";
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
    if (!els.stickerTable || !sticker) return;
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

  const bt = {
    status: document.getElementById("bt-status"),
    body: document.getElementById("bt-body"),
    lastStart: document.getElementById("bt-last-start"),
    lastStartNote: document.getElementById("bt-last-start-note"),
    gapMin: document.getElementById("bt-gap-min"),
    gapMedian: document.getElementById("bt-gap-median"),
    gapMax: document.getElementById("bt-gap-max"),
    nextDate: document.getElementById("bt-next-date"),
    nextNote: document.getElementById("bt-next-note"),
    srcLastStart: document.getElementById("bt-src-last-start"),
    srcGaps: document.getElementById("bt-src-gaps"),
    srcNext: document.getElementById("bt-src-next"),
    horizons: document.getElementById("bt-horizons"),
    noOutcomes: document.getElementById("bt-no-outcomes"),
    outcomes: document.getElementById("bt-outcomes"),
    hitRates: document.getElementById("bt-hit-rates"),
    latestLabel: document.getElementById("bt-latest-label"),
    performance: document.getElementById("bt-performance"),
    rowsTable: document.getElementById("bt-rows-table"),
    srcRows: document.getElementById("bt-src-rows"),
    projMarr: document.getElementById("bt-proj-marr"),
    projImplied: document.getElementById("bt-proj-implied"),
    projImpliedNote: document.getElementById("bt-proj-implied-note"),
    srcProj: document.getElementById("bt-src-proj"),
  };

  const BT_HIT_RATES = [
    ["moat_held_up", "Moat held through the hold"],
    ["price_target_hit", "Price target hit"],
    ["is_profitable", "Win rate (profitable)"],
  ];
  const BT_MOAT_CAPTION =
    "Moat counts as held only if the Big Five green count never fell below its signal-date level at any yearly checkpoint during the hold.";
  const BT_PERFORMANCE = [
    ["max_drawdown", "Max drawdown"],
    ["volatility", "Volatility (annualized)"],
    ["benchmark_return", "Benchmark return"],
    ["benchmark_delta", "vs. benchmark"],
  ];

  let btData = null;
  let btRequested = null;

  function setDays(el, d) {
    if (!el) return;
    if (d === null || d === undefined) {
      el.textContent = "n/a";
      return;
    }
    el.innerHTML = `${Math.round(d).toLocaleString()} days <span class="bt-note">≈ ${(d / 365.25).toFixed(1)} yr</span>`;
  }

  function fmtSignedPct(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return "n/a";
    return (v > 0 ? "+" : "") + fmtPct(v);
  }

  function fmtFlag(v) {
    return v === true ? "yes" : v === false ? "no" : "n/a";
  }

  function btShowStatus(message) {
    if (bt.body) bt.body.hidden = true;
    if (bt.status) {
      bt.status.hidden = false;
      bt.status.textContent = message;
    }
  }

  function renderBuyWindows(ticker, w) {
    if (!w || !bt.lastStart) return;
    const src = w.provenance || {};
    bt.lastStart.textContent = w.last_start || "none";
    if (bt.lastStartNote) {
      bt.lastStartNote.textContent = w.still_open
        ? `still open as of the latest signal (${w.latest_signal_date})`
        : `${w.starts ? w.starts.length : 0} window${w.starts && w.starts.length === 1 ? "" : "s"} in ${w.signal_rows} signals` +
          (w.latest_signal_date ? ` through ${w.latest_signal_date}` : "");
    }
    if (bt.srcLastStart) bt.srcLastStart.textContent = src.last_start || "";

    const g = w.gaps;
    setDays(bt.gapMin, g && g.min_days);
    setDays(bt.gapMedian, g && g.median_days);
    setDays(bt.gapMax, g && g.max_days);
    if (bt.srcGaps) {
      bt.srcGaps.textContent = g
        ? `${src.gaps || ""} ${g.n} gap${g.n === 1 ? "" : "s"}: ${g.gaps_days.map((d) => d.toLocaleString()).join(", ")} days.`
        : `${src.gaps || ""} Needs at least two buy-window starts; ${ticker} has ${w.starts ? w.starts.length : 0}.`;
    }

    const m = w.next_window_marker;
    if (bt.nextDate) bt.nextDate.textContent = m ? m.date : "n/a";
    if (bt.nextNote) {
      bt.nextNote.textContent = m
        ? `range ${m.earliest} to ${m.latest}` + (m.already_passed ? ` · already passed as of ${m.today}` : "") +
          " · illustrative, not a forecast"
        : "";
    }
    if (bt.srcNext) bt.srcNext.textContent = m ? src.next_window_marker : `${src.next_window_marker || ""} Not shown: no gap to measure.`;
  }

  function btTile(label, value, sub, source, tone) {
    return `
      <div class="stat-tile bt-tile ${tone || ""}">
        <span class="stat-name">${escapeHtml(label)}</span>
        <span class="stat-value ${tone || ""}">${escapeHtml(value)}</span>
        <span class="stat-window">${escapeHtml(sub)}</span>
        <p class="bt-src">Source: ${escapeHtml(source)}</p>
      </div>`;
  }

  function renderHorizon(ticker, h) {
    if (!btData || !btData.by_horizon || !btData.by_horizon[String(h)]) return;
    const block = btData.by_horizon[String(h)];

    if (bt.horizons) {
      bt.horizons.querySelectorAll(".chip").forEach((c) => {
        const active = c.dataset.horizon === String(h);
        c.classList.toggle("is-active", active);
        c.setAttribute("aria-pressed", String(active));
      });
    }

    if (bt.hitRates) {
      bt.hitRates.innerHTML = BT_HIT_RATES.map(([col, label]) => {
        const r = block.hit_rates ? block.hit_rates[col] : null;
        if (!r) return "";
        const sub = `${r.hits} of ${r.n} signals` + (r.missing ? ` (${r.missing} not yet known)` : "");
        return btTile(label, fmtPct(r.rate), sub, block.provenance && block.provenance.hit_rates ? block.provenance.hit_rates[col] : "");
      }).join("");
      if (!document.getElementById("bt-hit-rates-caption")) {
        const caption = document.createElement("p");
        caption.id = "bt-hit-rates-caption";
        caption.className = "bt-src";
        caption.textContent = BT_MOAT_CAPTION;
        bt.hitRates.insertAdjacentElement("afterend", caption);
      }
    }

    const latest = block.latest || {};
    if (bt.latestLabel) {
      bt.latestLabel.textContent =
        `latest signal ${latest.signal_date || "n/a"} → ${latest.target_date || "n/a"} (${h}-year hold)`;
    }

    if (bt.performance) {
      bt.performance.innerHTML = BT_PERFORMANCE.map(([col, label]) => {
        const v = latest[col];
        const signed = col === "benchmark_return" || col === "benchmark_delta";
        const tone = col === "benchmark_delta" && v != null ? (v >= 0 ? "is-green" : "is-red") : "";
        const sub = col === "benchmark_delta"
          ? `realized ${fmtSignedPct(latest.realized_return)} minus benchmark (equal-weighted avg. of the rule1.db tickers trading at the time, not the S&P 500 or the broader market)`
          : col === "max_drawdown" ? "peak to trough while held"
          : col === "volatility" ? "while held"
          : col === "benchmark_return" ? "equal-weighted avg. of the rule1.db tickers trading at the time, not the S&P 500 or the broader market"
          : "same holding period";
        const source = `backtest_outcomes.${col} for ${ticker}, signal_date = ${latest.signal_date}, horizon_years = ${h}.`;
        return btTile(label, signed ? fmtSignedPct(v) : fmtPct(v), sub, source, tone);
      }).join("");
    }

    if (bt.rowsTable && block.rows) {
      const cols = [
        ["Signal", (o) => o.signal_date],
        ["Target", (o) => o.target_date || "n/a"],
        ["Price at signal", (o) => fmtMoney(o.price_at_signal)],
        ["Realized price", (o) => fmtMoney(o.realized_price)],
        ["Realized return", (o) => fmtSignedPct(o.realized_return)],
        ["Projected", (o) => fmtSignedPct(o.projected_return)],
        ["Moat never dropped", (o) => fmtFlag(o.moat_held_up), "flag"],
        ["Target hit", (o) => fmtFlag(o.price_target_hit), "flag"],
        ["Profitable", (o) => fmtFlag(o.is_profitable), "flag"],
        ["Max DD", (o) => fmtPct(o.max_drawdown)],
        ["Vol", (o) => fmtPct(o.volatility)],
        ["Benchmark", (o) => fmtSignedPct(o.benchmark_return)],
        ["Delta", (o) => fmtSignedPct(o.benchmark_delta)],
      ];
      const head = `<tr>${cols.map(([name]) => `<th>${escapeHtml(name)}</th>`).join("")}</tr>`;
      const body = block.rows.map((o) => `<tr>${cols.map(([, fn, kind]) => {
        const text = fn(o);
        const cls = kind === "flag" ? (text === "yes" ? "is-yes" : text === "no" ? "is-no" : "") : "";
        return `<td class="${cls}">${escapeHtml(text)}</td>`;
      }).join("")}</tr>`).join("");
      bt.rowsTable.innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
    }

    if (bt.srcRows && block.provenance) bt.srcRows.textContent = `Source: ${block.provenance.rows}`;
  }

  function renderProjection(ticker, p) {
    if (!bt.projMarr) return;

    if (!p) {
      bt.projMarr.textContent = "n/a";
      if (bt.projImplied) bt.projImplied.textContent = "n/a";
      if (bt.projImpliedNote) bt.projImpliedNote.textContent = "Projection data unavailable";
      if (bt.srcProj) bt.srcProj.textContent = "Source: unavailable";
      return;
    }

    const marr = p.marr ?? 0.15;
    bt.projMarr.textContent = fmtPct(marr);
    const r = p.implied_annual_return;

    if (bt.projImplied) {
      bt.projImplied.textContent = fmtPct(r);
      bt.projImplied.className = "figure-value" + (r == null ? "" : r >= marr ? " is-green" : " is-red");
    }

    if (bt.projImpliedNote) {
      if (r == null) {
        bt.projImpliedNote.textContent = "needs a current price and a Sticker Price projection";
      } else {
        bt.projImpliedNote.textContent =
          `${fmtMoney(p.current_price)} today → ${fmtMoney(p.future_price)} in ${p.years} yr` +
          ` (Sticker Price ${fmtMoney(p.sticker_price)})`;
      }
    }

    if (bt.srcProj) {
      bt.srcProj.textContent =
        `Source: compute_sticker_price() using rule1.db's latest recorded price for ${ticker} (growth ${fmtPct(p.growth_rate)}, ` +
        `PE ${p.rule1_pe != null ? p.rule1_pe.toFixed(1) : "n/a"}). Implied return = ` +
        `(future price ÷ today's price)^(1/${p.years}) − 1, dividends excluded. Buying at exactly the ` +
        `Sticker Price gives the ${fmtPct(marr)} MARR.`;
    }
  }

  function renderBacktest(data) {
    btData = data;
    if (!data.available) {
      btShowStatus(`No backtest data: ${data.reason}`);
      return;
    }
    if (bt.status) bt.status.hidden = true;
    if (bt.body) bt.body.hidden = false;
    renderBuyWindows(data.ticker, data.buy_windows);

    if (!data.horizons || !data.horizons.length) {
      if (bt.horizons) bt.horizons.innerHTML = "";
      if (bt.outcomes) bt.outcomes.hidden = true;
      if (bt.noOutcomes) {
        bt.noOutcomes.hidden = false;
        bt.noOutcomes.textContent =
          `No rows for ${data.ticker} in backtest_outcomes yet, so there are no hit rates or performance metrics to show.`;
      }
      return;
    }
    if (bt.outcomes) bt.outcomes.hidden = false;
    if (bt.noOutcomes) bt.noOutcomes.hidden = true;
    if (bt.horizons) {
      bt.horizons.innerHTML = data.horizons.map((h) =>
        `<button type="button" class="chip" data-horizon="${h}" aria-pressed="false">${h} yr</button>`
      ).join("");
    }
    renderHorizon(data.ticker, data.default_horizon);
  }

  async function loadBacktest(ticker) {
    btRequested = ticker;
    btShowStatus(`Loading backtest for ${ticker}…`);
    try {
      const resp = await fetch(`/api/backtest/${encodeURIComponent(ticker)}`);
      const data = await resp.json();
      if (btRequested !== ticker) return;
      if (!resp.ok) throw new Error(`Request failed (${resp.status})`);
      renderBacktest(data);
    } catch (err) {
      if (btRequested === ticker) btShowStatus(`Couldn't load backtest data: ${err.message}`);
    }
  }

  if (bt.horizons) {
    bt.horizons.addEventListener("click", (e) => {
      const chip = e.target.closest(".chip");
      if (!chip || !btData || !btData.available) return;
      renderHorizon(btData.ticker, Number(chip.dataset.horizon));
    });
  }

  // Outbound links for current quotes, built from the ticker in the response.
  function renderLiveLinks(ticker) {
    if (!els.liveLinks) return;
    els.liveLinks.textContent = "";
    if (!ticker) {
      els.liveLinks.hidden = true;
      return;
    }
    const links = [
      ["Yahoo Finance", `https://finance.yahoo.com/quote/${encodeURIComponent(ticker.toUpperCase())}`],
      ["StockAnalysis", `https://stockanalysis.com/stocks/${encodeURIComponent(ticker.toLowerCase())}/`],
    ];
    for (const [label, href] of links) {
      const a = document.createElement("a");
      a.href = href;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = label;
      els.liveLinks.appendChild(a);
    }
    const note = document.createElement("span");
    note.className = "live-links-note";
    note.textContent = "Dashboard prices are as of the last close in the data. Use these for current quotes.";
    els.liveLinks.appendChild(note);
    els.liveLinks.hidden = false;
  }

  function render(data) {
    currentData = data;

    if (els.name) els.name.textContent = data.name || data.ticker;
    if (els.ticker) els.ticker.textContent = data.ticker;
    if (els.sector) els.sector.textContent = [data.sector, data.industry].filter(Boolean).join(" — ");
    renderLiveLinks(String(data.ticker || "").trim());
    if (els.price) els.price.textContent = fmtMoney(data.current_price);
    if (els.priceLabel) {
      // "YYYY-MM-DD" -> "MM/DD/YYYY" by splitting the string, so no timezone shift
      const parts = (data.current_price_date || "").split("-");
      els.priceLabel.textContent = parts.length === 3
        ? `Price on ${parts[1]}/${parts[2]}/${parts[0]}`
        : "Current price";
    }
    if (els.sticker && data.sticker) els.sticker.textContent = fmtMoney(data.sticker.sticker_price);
    if (els.mos && data.sticker) els.mos.textContent = fmtMoney(data.sticker.mos_price);

    if (els.verdict && data.sticker) {
      els.verdict.textContent = data.sticker.verdict || "No verdict available.";
      els.verdict.className = `verdict-pill ${verdictClass(data.sticker.verdict)}`;
    }

    if (els.warningBanner) {
      if (data.warnings && data.warnings.length) {
        els.warningBanner.hidden = false;
        els.warningBanner.textContent = data.warnings.join(" ");
      } else {
        els.warningBanner.hidden = true;
        els.warningBanner.textContent = "";
      }
    }

    if (data.big_five) {
      renderBigFive(data.big_five);
      selectBigFiveMetric(activeMetric && data.big_five[activeMetric] ? activeMetric : "sales");
    }

    if (data.moat) {
      if (els.moatRatio) els.moatRatio.textContent = `${data.moat.green_count} / ${data.moat.total}`;
      if (els.moatImage) {
        els.moatImage.src = `/api/moat/${data.moat.level}.png`;
        els.moatImage.alt = `Castle graphic showing ${data.moat.green_count} of ${data.moat.total} Big Five numbers green`;
      }
    }

    const bust = Date.now();
    showImageOrPlaceholder(els.growthChart, els.growthPlaceholder,
      `/api/chart/growth/${encodeURIComponent(data.ticker)}.png?_=${bust}`);
    showImageOrPlaceholder(els.debtChart, els.debtPlaceholder,
      `/api/chart/debt/${encodeURIComponent(data.ticker)}.png?_=${bust}`,
      fitLeadershipCard);

    if (data.sticker) renderStickerTable(data.sticker);
    renderLeadership(data.leadership);
    requestAnimationFrame(fitLeadershipCard);

    if (els.sourcesList) {
      els.sourcesList.innerHTML = (data.sources || []).map((src) =>
        /^https?:\/\//.test(src)
          ? `<li><a href="${escapeHtml(src)}" target="_blank" rel="noopener">${escapeHtml(src)}</a></li>`
          : `<li>${escapeHtml(src)}</li>`
      ).join("") || "<li>No source links available.</li>";
    }

    if (results) results.hidden = false;
    renderProjection(data.ticker, data.projection);
    loadBacktest(data.ticker);
  }

  async function runSearch(rawTicker) {
    const ticker = (rawTicker || "").trim().toUpperCase();
    if (!ticker) {
      setError("Type a ticker symbol first.");
      return;
    }
    if (input) input.value = ticker;
    if (SUPPORTED_TICKERS.length && !SUPPORTED_TICKERS.includes(ticker)) {
      if (results) results.hidden = true;
      setError(`Sorry, "${ticker}" isn't available. This app only supports these ` +
        `${SUPPORTED_TICKERS.length} tickers: ${SUPPORTED_TICKERS.join(", ")}.`);
      return;
    }
    setLoading(ticker);
    const submitBtn = form ? form.querySelector("button") : null;
    if (submitBtn) submitBtn.disabled = true;

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
      if (results) results.hidden = true;
      setError(err.message || "Something went wrong loading that ticker.");
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  if (form) {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      runSearch(input.value);
    });
  }

  if (quickPicks) {
    quickPicks.addEventListener("click", (e) => {
      const btn = e.target.closest(".chip");
      if (!btn) return;
      runSearch(btn.dataset.ticker);
    });
  }

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(fitLeadershipCard, 120);
  });

  const params = new URLSearchParams(window.location.search);
  const initial = params.get("ticker") || "AAPL";
  runSearch(initial);
})();