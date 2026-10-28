// FinAssist page: the dropdown chain AMC -> Fund -> Customer -> Folio -> results.
// Plain JavaScript, no framework. Every call goes to web-ui itself (/api/v1/...), which
// forwards it to the right backend service.

const $ = (id) => document.getElementById(id);
const amcSelect = $("amc"), fundSelect = $("fund"), holderSelect = $("holder"),
      folioSelect = $("folio"), folioLabel = $("folio-label");

// Folios of each holder of the chosen fund, from the holders answer: {"User1": ["F-1", ...]}.
let foliosByHolder = {};

// Increases with every user choice. An answer that arrives after a NEWER choice was made is
// ignored, so a slow old answer can never overwrite the screen for the current choice.
let generation = 0;


// ------------------------------------------------------------------ helpers
async function getJSON(path, params = {}) {
  // URLSearchParams encodes every value (spaces, "&", "/" ...) safely.
  const query = new URLSearchParams(params).toString();
  const response = await fetch(query ? `${path}?${query}` : path);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail;
    throw new Error(typeof detail === "string" ? detail
                    : detail?.message ?? `request failed (${response.status})`);
  }
  return body;
}

function fill(select, items, placeholder) {
  // items: [{value, label}]. Option text is set as TEXT, never as HTML (no script injection).
  select.replaceChildren();
  if (placeholder) {
    select.add(new Option(placeholder, "", true, true));
    select.options[0].disabled = true;
  }
  for (const { value, label } of items) select.add(new Option(label, value));
  select.disabled = items.length === 0;
}

function clear(...selects) {
  for (const select of selects) { select.replaceChildren(); select.disabled = true; }
}

function showFolio(visible) {
  folioSelect.hidden = folioLabel.hidden = !visible;
}

function clearResults() {
  $("results").hidden = true;
  $("raw").textContent = "";
}

function say(text, isError = false) {
  $("message").textContent = text;
  $("message").className = isError ? "error" : "";
}


// ------------------------------------------------------------------ the chain
async function loadAmcs() {
  try {
    say("Loading AMCs…");
    const { amcs } = await getJSON("/api/v1/amcs");
    fill(amcSelect, amcs.map((name) => ({ value: name, label: name })), "Choose an AMC");
    say(amcs.length ? "" : "No investments found.");
  } catch (error) {
    say(error.message, true);
  }
}

async function onAmcChosen() {
  const mine = ++generation;
  clear(fundSelect, holderSelect, folioSelect); showFolio(false); clearResults();
  try {
    say("Loading funds…");
    const { schemes } = await getJSON("/api/v1/schemes", { amc: amcSelect.value });
    if (mine !== generation) return;                  // the user has chosen again meanwhile
    fill(fundSelect, schemes.map((s) => ({ value: s.isin, label: s.scheme_name })), "Choose a fund");
    say("");
  } catch (error) {
    if (mine === generation) say(error.message, true);
  }
}

async function onFundChosen() {
  const mine = ++generation;
  clear(holderSelect, folioSelect); showFolio(false); clearResults();
  try {
    say("Loading customers…");
    const isin = encodeURIComponent(fundSelect.value);
    const { holders } = await getJSON(`/api/v1/isins/${isin}/holders`);
    if (mine !== generation) return;
    foliosByHolder = Object.fromEntries(holders.map((h) => [h.name, h.folios]));
    const items = holders.map((h) => ({ value: h.name, label: h.name }));
    say("");
    if (holders.length === 1) {
      // Always show the customer box; with one customer it is simply pre-selected.
      fill(holderSelect, items);
      onHolderChosen();
    } else {
      fill(holderSelect, items, "Choose a customer");
    }
  } catch (error) {
    if (mine === generation) say(error.message, true);
  }
}

function onHolderChosen() {
  generation++;
  clearResults();
  const folios = foliosByHolder[holderSelect.value] ?? [];
  if (folios.length > 1) {
    fill(folioSelect, folios.map((f) => ({ value: f, label: f })), "Choose a folio");
    showFolio(true);
  } else {
    clear(folioSelect); showFolio(false);
    loadResults(folios[0]);                            // one folio: calculate straight away
  }
}

function onFolioChosen() {
  loadResults(folioSelect.value);
}

async function loadResults(folio) {
  const mine = ++generation;
  clearResults();
  try {
    say("Calculating…");
    const answer = await getJSON("/api/v1/xirr",
      { isin: fundSelect.value, holder: holderSelect.value, folio });
    if (mine !== generation) return;
    say("");
    showResults(answer);
  } catch (error) {
    if (mine === generation) say(error.message, true);
  }
}

function showResults(answer) {
  // Step 7.2: show the raw answer. Step 7.3 replaces this with proper sections.
  $("raw").textContent = JSON.stringify(answer, null, 2);
  $("results").hidden = false;
}


// ------------------------------------------------------------------ start
amcSelect.addEventListener("change", onAmcChosen);
fundSelect.addEventListener("change", onFundChosen);
holderSelect.addEventListener("change", onHolderChosen);
folioSelect.addEventListener("change", onFolioChosen);
loadAmcs();
