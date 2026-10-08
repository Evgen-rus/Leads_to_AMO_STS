const API = "/api";
const ACCOUNT = "450458.amocrm.ru";
const DEFAULT_PIPELINE = "11105258";
const PAGE_SIZE = 10;
const $ = (id) => document.getElementById(id);
const state = { pipelines: [], pipelineById: new Map(), stageById: new Map(), deals: [], page: 1, nextPage: null, previousPages: [], selected: new Set(), activeDealId: null, listController: null, detailController: null, detailTab: "conversation", detail: null, busy: false };

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function value(obj, ...keys) {
  for (const key of keys) if (obj?.[key] !== undefined && obj[key] !== null && obj[key] !== "") return obj[key];
  return "";
}
function idOf(item) { return String(value(item, "id")); }
function escDate(raw) {
  const date = raw ? new Date(typeof raw === "number" ? raw * 1000 : raw) : null;
  return date && !Number.isNaN(date.getTime()) ? new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Novosibirsk" }).format(date) : "—";
}
function setStatus(message, kind = "") {
  const status = $("app-status"); status.className = `status ${kind}`;
  status.replaceChildren(element("span", "status-dot"), element("span", "", message));
}
function setBusy(busy) {
  state.busy = busy;
  for (const id of ["apply-filters", "refresh", "export-button", "direct-id"]) $(id).disabled = busy || (id === "export-button" && state.selected.size === 0);
  $("pipeline-select").disabled = busy;
  for (const id of ["date-from", "date-to", "query", "ours-only"]) $(id).disabled = busy;
  $("stage-options").querySelectorAll("input").forEach((input) => { input.disabled = busy; });
  document.querySelectorAll("input[name=material]").forEach((input) => { input.disabled = busy; });
  $("add-detail").disabled = busy || (!state.selected.has(String(state.activeDealId)) && state.selected.size >= 10);
  $("select-page").disabled = busy || !state.deals.length || state.selected.size >= 10;
  $("clear-selection").disabled = busy;
  $("prev-page").disabled = busy || state.previousPages.length === 0;
  $("next-page").disabled = busy || !state.nextPage;
}
async function request(path, options = {}, signal) {
  const response = await fetch(`${API}${path}`, { ...options, signal, headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers } });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { const body = await response.json(); message = body.detail || body.error || message; } catch {}
    throw new Error(message);
  }
  return response;
}
async function json(path, options, signal) { return (await request(path, options, signal)).json(); }
function pipelineName(pipeline) {
  const archived = pipeline.is_archived || pipeline.is_deleted || pipeline.archived;
  return `${value(pipeline, "name") || `Воронка ${idOf(pipeline)}`}${archived ? " · архив" : ""}`;
}
function statusesOf(pipeline) { return pipeline?._embedded?.statuses || pipeline?.statuses || []; }
function selectedStages() { return [...$("stage-options").querySelectorAll("input:checked")].map((input) => input.value); }
function renderPipelines() {
  const select = $("pipeline-select"); select.replaceChildren();
  const active = state.pipelines.filter((p) => !p.is_archive && !p.is_archived && !p.is_deleted && !p.archived);
  state.pipelines = active;
  for (const p of active) { const option = element("option", "", value(p, "name") || `Воронка ${idOf(p)}`); option.value = idOf(p); select.append(option); }
  if (!state.pipelineById.has(DEFAULT_PIPELINE)) state.pipelineById = new Map(state.pipelines.map((p) => [idOf(p), p]));
  select.disabled = !state.pipelines.length;
  select.value = state.pipelineById.has(DEFAULT_PIPELINE) ? DEFAULT_PIPELINE : (active[0] ? idOf(active[0]) : idOf(state.pipelines[0] || {}));
  renderStages();
}
function renderStages() {
  const host = $("stage-options"); host.replaceChildren();
  const stages = statusesOf(state.pipelineById.get($("pipeline-select").value));
  state.stageById.clear();
  for (const stage of stages) state.stageById.set(idOf(stage), stage);
  if (!stages.length) { host.append(element("span", "muted", "У воронки не найдены этапы")); $("stage-summary").textContent = "Все этапы"; return; }
  for (const stage of stages) {
    const label = element("label", "stage-option");
    const check = document.createElement("input"); check.type = "checkbox"; check.value = idOf(stage); check.addEventListener("change", updateStageSummary);
    label.append(check, element("span", "stage-color", ""), element("span", "stage-name", value(stage, "name") || `Этап ${idOf(stage)}`));
    const color = value(stage, "color"); if (color) label.querySelector(".stage-color").style.backgroundColor = color;
    host.append(label);
  }
  updateStageSummary();
}
function updateStageSummary() {
  const count = selectedStages().length;
  $("stage-summary").textContent = count ? `Выбрано: ${count}` : "Все этапы";
}
function listParams(page = 1) {
  const params = new URLSearchParams({ pipeline_id: $("pipeline-select").value, page: String(page), ours: $("ours-only").checked ? "1" : "0" });
  const stages = selectedStages(); if (stages.length) params.set("status_ids", stages.join(","));
  if ($("date-from").value) params.set("created_from", $("date-from").value);
  if ($("date-to").value) params.set("created_to", $("date-to").value);
  if ($("query").value.trim()) params.set("query", $("query").value.trim());
  return params;
}
function renderDeals() {
  const body = $("deal-rows"); body.replaceChildren();
  if (!state.deals.length) { const row = element("tr"); const cell = element("td", "empty", "По этому фильтру сделок нет."); cell.colSpan = 4; row.append(cell); body.append(row); }
  for (const deal of state.deals) {
    const row = element("tr", state.activeDealId === idOf(deal) ? "active-row" : "");
    const checkCell = element("td", "select-col"); const check = document.createElement("input"); check.type = "checkbox"; check.checked = state.selected.has(idOf(deal)); check.disabled = state.busy || (!check.checked && state.selected.size >= 10); check.setAttribute("aria-label", `Добавить сделку ${value(deal, "name") || idOf(deal)} в выгрузку`);
    check.addEventListener("change", () => { if (check.checked) state.selected.add(idOf(deal)); else state.selected.delete(idOf(deal)); updateSelection(); renderDeals(); }); checkCell.append(check);
    const dealCell = element("td", "deal-cell"); const link = element("button", "deal-link", value(deal, "name") || `Сделка ${idOf(deal)}`); link.type = "button"; link.addEventListener("click", () => loadDetail(idOf(deal))); dealCell.append(link, element("span", "deal-meta", `#${idOf(deal)}${deal.is_ours ? " · наша" : ""}`));
    const stage = state.stageById.get(String(value(deal, "status_id")));
    const stageCell = element("td"); const badge = element("span", "stage-badge", value(stage, "name") || String(value(deal, "status_id") || "—")); if (value(stage, "color")) badge.style.setProperty("--stage-color", stage.color); stageCell.append(badge);
    const created = element("td", "date-cell", escDate(value(deal, "created_at"))); row.append(checkCell, dealCell, stageCell, created); body.append(row);
  }
  $("page-label").textContent = `Страница ${state.page}`;
  $("prev-page").disabled = state.previousPages.length === 0 || state.busy;
  $("next-page").disabled = !state.nextPage || state.busy;
  $("list-caption").textContent = `${state.deals.length} в списке · по 10 на странице`;
}
function updateSelection() {
  $("selected-count").textContent = String(state.selected.size);
  $("export-button").disabled = state.busy || !state.selected.size;
  $("select-page").disabled = state.busy || !state.deals.length || state.selected.size >= 10;
  renderDeals();
  syncDetailSelection();
}
async function loadDeals(page = 1, preserveHistory = false) {
  if ($("date-from").value && $("date-to").value && $("date-from").value > $("date-to").value) {
    $("filter-message").textContent = "Дата начала позже даты окончания."; return;
  }
  state.listController?.abort(); state.listController = new AbortController();
  const signal = state.listController.signal; state.page = page; state.nextPage = null; state.deals = [];
  if (!preserveHistory) state.previousPages = [];
  $("list-caption").textContent = "Загружаю…"; renderDeals(); setBusy(true); setStatus("Загружаю сделки", "busy");
  try {
    const data = await json(`/leads?${listParams(page)}`, undefined, signal);
    if (signal.aborted) return;
    state.deals = data.items || []; state.nextPage = data.next_page ?? null; renderDeals();
    $("filter-message").textContent = ""; setStatus("Готово");
  } catch (error) {
    if (error.name === "AbortError") return;
    state.deals = []; renderDeals(); $("list-caption").textContent = "Не удалось загрузить"; $("filter-message").textContent = error.message; setStatus("Ошибка загрузки", "error");
  } finally { if (!signal.aborted) { setBusy(false); renderDeals(); } }
}
function field(label, content) { const wrap = element("div", "data-field"); wrap.append(element("span", "data-label", label), element("span", "data-value", String(content || "—"))); return wrap; }
function dealTitle(deal) { return value(deal, "name") || `Сделка ${idOf(deal)}`; }
function asText(value) {
  if (Array.isArray(value)) return value.map(asText).filter(Boolean).join(", ");
  if (value && typeof value === "object") return String(value.value ?? value.name ?? value.text ?? JSON.stringify(value));
  return String(value ?? "");
}
function customFields(deal) {
  const result = [];
  for (const item of deal.custom_fields_values || []) {
    const name = item.field_name || item.name || `Поле ${item.field_id || ""}`;
    result.push([name, (item.values || []).map((v) => asText(v.value ?? v)).join(", ")]);
  }
  return result;
}
function unwrapText(item) { return value(item, "text", "note", "message", "body", "comment", "description", "transcription", "summary") || value(item?.params, "text", "message", "body", "transcription", "summary") || ""; }
function isAudio(note) { const type = String(value(note, "note_type", "type", "entity_type")).toLowerCase(); return Boolean(value(note?.params, "link")) || /call_(?:in|out)|audio|звон/i.test(type); }
function isTranscript(note) { return /transcri|transcript|расшиф|транскр|диалог/i.test(`${value(note, "note_type", "type")} ${unwrapText(note)}`) || /^\s*(?:User|Пользователь)\s*:/i.test(unwrapText(note)); }
function normalizeText(text) { return String(text || "").replace(/\s+/g, " ").trim(); }
function recordTypeLabel(item) {
  const type = String(value(item, "note_type", "type", "event_type", "task_type", "entity_type") || "").toLowerCase();
  return ({ call_out: "Исходящий звонок", call_in: "Входящий звонок", common: "Комментарий" })[type] || type || "Запись";
}
function itemsBlock(items, emptyText, audioActions = false) {
  const section = element("div", "item-list");
  if (!items?.length) section.append(element("p", "muted", emptyText));
  for (const item of items || []) {
    const card = element("article", "record-card");
    const heading = element("div", "record-heading"); heading.append(element("strong", "", recordTypeLabel(item)), element("time", "", escDate(value(item, "created_at", "updated_at", "date")))); card.append(heading);
    const text = unwrapText(item); if (text) card.append(element("p", "record-text", text));
    const params = item.params || {};
    const transcription = value(params, "transcription", "transcript", "transcription_text") || value(item, "transcription", "transcript", "transcription_text");
    if (transcription && transcription !== text) { card.append(element("span", "data-label", "Транскрибация"), element("p", "record-text", transcription)); }
    const summary = value(params, "summary", "result", "call_result") || value(item, "summary");
    if (summary && summary !== text) { card.append(element("span", "data-label", "Резюме"), element("p", "record-text", summary)); }
    if (audioActions && isAudio(item)) {
      const audio = document.createElement("audio"); audio.controls = true; audio.preload = "none"; audio.setAttribute("aria-label", "Запись разговора");
      const load = element("button", "text-button", "Загрузить аудио"); load.type = "button";
      load.addEventListener("click", async () => {
        const leadId = state.activeDealId;
        load.disabled = true; load.textContent = "Получаю запись…";
        try {
          const response = await request(`/audio?lead_id=${encodeURIComponent(leadId)}&note_id=${encodeURIComponent(idOf(item))}`);
          const type = response.headers.get("content-type") || "";
          if (type.includes("json")) {
            const media = await response.json(); const url = media.url || media.audio_url || media.download_url;
            if (!url) throw new Error(media.detail || "Ссылка на аудиозапись не найдена");
            audio.src = url;
            const download = element("a", "download-link", "Скачать аудио"); download.href = url; download.download = media.filename || "audio"; card.append(audio, download);
          } else {
            const blob = await response.blob(); const url = URL.createObjectURL(blob); audio.src = url;
            const download = element("a", "download-link", "Скачать аудио"); download.href = url; download.download = response.headers.get("content-disposition")?.match(/filename="?([^";]+)/)?.[1] || `lead-${leadId}.wav`; card.append(audio, download);
          }
          load.remove();
        } catch (error) { load.disabled = false; load.textContent = "Повторить загрузку аудио"; card.append(element("span", "inline-error", error.message)); }
      }); card.append(load);
    }
    section.append(card);
  }
  return section;
}
function renderTab() {
  const host = $("detail-content"); host.replaceChildren();
  const detail = state.detail; if (!detail) return;
  const deal = detail.lead || {};
  const tabs = element("div", "tabs");
  for (const [key, title] of [["conversation", "Звонки"], ["history", "История"], ["comments", "Комментарии"], ["data", "Данные"]]) {
    const button = element("button", `tab ${state.detailTab === key ? "selected" : ""}`, title); button.type = "button"; button.setAttribute("aria-pressed", String(state.detailTab === key)); button.addEventListener("click", () => { state.detailTab = key; renderTab(); }); tabs.append(button);
  }
  host.append(tabs);
  const body = element("div", "tab-content"); host.append(body);
  if (detail.warnings?.length) { const warnings = element("div", "warning-box"); for (const warning of detail.warnings) warnings.append(element("p", "", typeof warning === "string" ? warning : JSON.stringify(warning))); body.append(warnings); }
  if (state.detailTab === "conversation") {
    const notes = detail.notes || [];
    body.append(itemsBlock(notes.filter(isAudio), "Примечаний о звонках нет.", true));
    const deal = detail.lead || {};
    const transcriptField = (deal.custom_fields_values || []).find((field) => String(field.field_id) === "1550725" || /транскриб/i.test(value(field, "field_name", "name", "label")));
    const transcriptTexts = (transcriptField?.values || []).map((v) => asText(v.value)).filter(Boolean);
    const summaries = [];
    for (const note of notes.filter((n) => !isAudio(n))) {
      const text = unwrapText(note);
      const marker = "Полный диалог по ролям:";
      const splitAt = text.indexOf(marker);
      if (splitAt >= 0) {
        const summary = text.slice(0, splitAt).trim();
        const embeddedTranscript = text.slice(splitAt + marker.length).trim();
        if (summary) summaries.push(summary);
        if (embeddedTranscript) transcriptTexts.push(embeddedTranscript);
      } else if (/резюме|summary/i.test(`${value(note, "note_type", "type")} ${text}`)) summaries.push(text);
      else if (isTranscript(note)) transcriptTexts.push(text);
    }
    if (summaries.length) { body.append(element("h3", "subsection-title", "Резюме")); for (const summary of [...new Set(summaries.map(normalizeText))]) body.append(element("pre", "transcript summary-text", summary)); }
    const uniqueTranscripts = [...new Map(transcriptTexts.map((text) => [normalizeText(text), text])).values()];
    if (uniqueTranscripts.length) { body.append(element("h3", "subsection-title", "Транскрибация")); for (const transcript of uniqueTranscripts) body.append(element("pre", "transcript", transcript)); }
    if (!notes.some(isAudio) && !uniqueTranscripts.length && !summaries.length) body.append(element("p", "muted", "Звонки и транскрибации не найдены."));
  } else if (state.detailTab === "history") {
    const events = detail.events || [];
    if (!events.length) body.append(element("p", "muted", "История событий недоступна или пуста."));
    else {
      const timeline = element("div", "timeline");
      for (const event of events) {
        const item = element("article", "timeline-item");
        item.append(element("time", "", escDate(value(event, "created_at", "updated_at", "date"))));
        item.append(element("strong", "", eventLabel(event, deal)));
        const description = eventDescription(event);
        if (description) item.append(element("p", "record-text", description));
        timeline.append(item);
      }
      body.append(timeline);
    }
  } else if (state.detailTab === "comments") {
    const comments = (detail.notes || []).filter((n) => !isAudio(n));
    body.append(itemsBlock(comments, "Комментариев нет."));
    if (detail.tasks?.length) { body.append(element("h3", "subsection-title", "Задачи"), itemsBlock(detail.tasks, "Задач нет.")); }
  } else {
    const pipeline = state.pipelineById.get(String(value(deal, "pipeline_id")));
    const stage = state.pipelineById.get(String(value(deal, "pipeline_id"))) ? statusesOf(pipeline).find((s) => idOf(s) === String(value(deal, "status_id"))) : null;
    const fields = [
      ["ID сделки", idOf(deal)], ["Воронка", value(pipeline, "name") || value(deal, "pipeline_id")], ["Этап", value(stage, "name") || value(deal, "status_id")],
      ["Создана", escDate(value(deal, "created_at"))], ["Изменена", escDate(value(deal, "updated_at"))], ["Ответственный", detail.responsible_name || value(deal, "responsible_name") || value(deal, "responsible_user_id")],
      ["Источник", value(detail.source, "name") || "—"], ["Наш исходный ID", value(detail.source, "id")], ["Канал", value(detail.source, "channel")], ["Сделка из нашего проекта", detail.is_ours ? "Да" : "Не подтверждено"],
    ];
    const grid = element("div", "data-grid"); for (const [label, val] of fields) grid.append(field(label, val));
    for (const [label, val] of customFields(deal)) grid.append(field(label, val));
    body.append(grid);
  }
}
function eventLabel(event) {
  const rawType = String(value(event, "event_type", "type", "action") || "").toLowerCase();
  if (/status|stage|статус|этап/.test(rawType)) return "Изменение этапа";
  if (/create/.test(rawType)) return "Сделка создана";
  if (/responsible|user/.test(rawType)) return "Изменение ответственного";
  return value(event, "event_type_name", "type_name", "name") || value(event, "event_type", "type") || "Событие";
}
function eventDescription(event) {
  const params = event.params || {};
  const changes = (raw) => (Array.isArray(raw) ? raw : [raw]).map((change) => {
    if (!change || typeof change !== "object") return { id: change };
    const status = change.lead_status || change.status || change;
    return { id: value(status, "id", "status_id", "value", "name"), pipelineId: value(status, "pipeline_id", "pipelineId") };
  }).filter((item) => item.id);
  const before = changes(event.value_before || value(event, "old_status_id", "from_status_id") || value(params, "old_status_id", "from_status_id"))[0];
  const after = changes(event.value_after || value(event, "new_status_id", "to_status_id") || value(params, "new_status_id", "to_status_id"))[0];
  const statusName = (item) => {
    if (!item) return "—";
    const pipeline = item.pipelineId ? state.pipelineById.get(String(item.pipelineId)) : null;
    return statusesOf(pipeline).find((s) => idOf(s) === String(item.id))?.name || String(item.id);
  };
  if (before || after) return `${statusName(before)} → ${statusName(after)}`;
  const description = value(event, "text", "description", "value", "note");
  if (description) return asText(description);
  return value(params, "name", "text", "value", "field_name") ? asText(value(params, "name", "text", "value", "field_name")) : "";
}
async function loadDetail(id) {
  state.detailController?.abort(); state.detailController = new AbortController(); const signal = state.detailController.signal;
  state.activeDealId = String(id); state.detail = null; state.detailTab = "conversation"; $("detail-title").textContent = `Сделка ${id}`; $("open-crm").hidden = false; $("add-detail").hidden = false; syncDetailSelection(); $("open-crm").onclick = () => window.open(`https://${ACCOUNT}/leads/detail/${encodeURIComponent(id)}`, "_blank", "noopener,noreferrer");
  $("detail-content").replaceChildren(element("p", "muted", "Загружаю карточку сделки…")); renderDeals();
  try {
    const data = await json(`/leads/${encodeURIComponent(id)}`, undefined, signal);
    if (signal.aborted || state.activeDealId !== String(id)) return;
    state.detail = data; $("detail-title").textContent = dealTitle(data.lead || {}); renderTab();
    syncDetailSelection();
  } catch (error) {
    if (error.name === "AbortError") return;
    $("detail-content").replaceChildren(element("div", "error-box", `Не удалось загрузить сделку: ${error.message}`));
  }
}
function syncDetailSelection() {
  const button = $("add-detail"); if (!button || button.hidden) return;
  const selected = state.selected.has(String(state.activeDealId));
  button.textContent = selected ? "Убрать из пакета" : "В пакет";
  button.disabled = state.busy || (!selected && state.selected.size >= 10);
}
function parseDirect(raw) {
  const text = raw.trim();
  if (/^\d+$/.test(text)) return text;
  try {
    const url = new URL(text);
    if (url.hostname.toLowerCase() !== ACCOUNT || url.protocol !== "https:") throw new Error(`Разрешены ссылки только с https://${ACCOUNT}`);
    const match = url.pathname.match(/^\/leads\/detail\/(\d+)\/?$/); if (!match) throw new Error("В ссылке не найден ID сделки"); return match[1];
  } catch (error) { if (error instanceof TypeError) throw new Error("Введите числовой ID или полную ссылку amoCRM"); throw error; }
}
async function exportSelected() {
  const ids = [...state.selected]; if (!ids.length || ids.length > 10) return;
  const materials = [...document.querySelectorAll("input[name=material]:checked")].map((item) => item.value);
  if (!materials.length) { $("export-result").textContent = "Выберите хотя бы один тип материалов."; return; }
  $("export-result").replaceChildren(element("span", "busy-indicator", "Собираю архив…")); setBusy(true);
  try {
    const data = await json("/export", { method: "POST", body: JSON.stringify({ lead_ids: ids, materials }) });
    const message = element("p", "", `Готово: ${data.exported_count ?? ids.length} сделок${data.folder ? ` · папка ${data.folder}` : ""}`); $("export-result").replaceChildren(message);
    if (data.download_url || data.folder) {
      const name = data.download_url?.split("name=").pop() || data.folder;
      const download = element("a", "button download-button", "Скачать ZIP"); download.href = data.download_url || `/api/download?name=${encodeURIComponent(name)}`; download.download = "amocrm-export.zip"; $("export-result").append(download);
    }
    if (data.warnings?.length) { const box = element("div", "warning-box"); for (const warning of data.warnings) box.append(element("p", "", typeof warning === "string" ? warning : JSON.stringify(warning))); $("export-result").append(box); }
    setStatus("Архив готов");
  } catch (error) { $("export-result").replaceChildren(element("div", "error-box", `Не удалось собрать архив: ${error.message}`)); setStatus("Ошибка выгрузки", "error"); }
  finally { setBusy(false); updateSelection(); }
}
async function init() {
  try {
    const data = await json("/pipelines"); state.pipelines = data.items || []; state.pipelineById = new Map(state.pipelines.map((p) => [idOf(p), p]));
    if (!state.pipelines.length) throw new Error("amoCRM не вернула воронки");
    renderPipelines(); setStatus("Подключено"); await loadDeals(1);
  } catch (error) { $("pipeline-select").replaceChildren(element("option", "", "Ошибка подключения")); $("deal-rows").replaceChildren(); const cell = element("td", "empty", `Не удалось подключиться: ${error.message}`); cell.colSpan = 4; const row = element("tr"); row.append(cell); $("deal-rows").append(row); setStatus("Нет подключения", "error"); }
}
$("pipeline-select").addEventListener("change", () => { renderStages(); loadDeals(1); });
$("apply-filters").addEventListener("click", () => loadDeals(1));
$("query").addEventListener("keydown", (event) => { if (event.key === "Enter") loadDeals(1); });
$("refresh").addEventListener("click", () => loadDeals(state.page, true));
$("prev-page").addEventListener("click", () => { const page = state.previousPages.pop(); if (page) loadDeals(page, true); });
$("next-page").addEventListener("click", () => { if (state.nextPage) { state.previousPages.push(state.page); loadDeals(state.nextPage, true); } });
$("reset-filters").addEventListener("click", () => { $("pipeline-select").value = state.pipelineById.has(DEFAULT_PIPELINE) ? DEFAULT_PIPELINE : $("pipeline-select").options[0]?.value; $("date-from").value = ""; $("date-to").value = ""; $("query").value = ""; $("ours-only").checked = false; renderStages(); loadDeals(1); });
$("clear-selection").addEventListener("click", () => { state.selected.clear(); updateSelection(); });
$("select-page").addEventListener("click", () => {
  for (const deal of state.deals) { if (state.selected.size >= 10) break; state.selected.add(idOf(deal)); }
  updateSelection();
});
$("add-detail").addEventListener("click", () => {
  const id = String(state.activeDealId); if (!id) return;
  if (state.selected.has(id)) state.selected.delete(id);
  else if (state.selected.size < 10) state.selected.add(id);
  updateSelection(); syncDetailSelection();
});
$("direct-form").addEventListener("submit", (event) => { event.preventDefault(); try { const id = parseDirect($("direct-id").value); $("filter-message").textContent = ""; loadDetail(id); } catch (error) { $("filter-message").textContent = error.message; } });
$("export-button").addEventListener("click", exportSelected);
init();
