import { useEffect, useState } from "react";
import { api } from "../api";
import { PROVIDER_LABEL } from "../format";
import type { AgentConfig, Meta, PresetInfo, Provider } from "../types";

interface Props {
  meta: Meta;
  initial: AgentConfig | null; // null = create
  onClose: () => void;
  onSaved: () => void;
}

const LLM_SUGGESTIONS = [
  "anthropic/claude-haiku-4.5", "anthropic/claude-sonnet-4.5", "openai/gpt-5-mini", "openai/gpt-5",
  "google/gemini-2.5-flash", "google/gemini-2.5-pro", "deepseek/deepseek-chat", "x-ai/grok-4-fast", "qwen/qwen3-235b-a22b",
];
const LAYA_MODELS = ["", "english", "multilingual", "typed-decisions"];
const DEFAULT_MODEL: Partial<Record<Provider, string>> = {
  jev: "~typesafe/jev-latest", laya: "typed-decisions", gliner: "urchade/gliner_medium-v2.1",
  llm: "anthropic/claude-haiku-4.5", baseline: "sma_cross", systemone: "",
};
const LOCAL: Provider[] = ["laya", "gliner", "systemone"];

type Path = (string | number)[];

function setIn<T>(obj: T, path: Path, value: unknown): T {
  const copy = structuredClone(obj) as Record<string | number, unknown>;
  let cur = copy as Record<string | number, unknown>;
  for (const k of path.slice(0, -1)) cur = cur[k] as Record<string | number, unknown>;
  cur[path[path.length - 1]] = value;
  return copy as T;
}

export function AgentForm({ meta, initial, onClose, onSaved }: Props) {
  const [cfg, setCfg] = useState<AgentConfig>(() => structuredClone(initial ?? { ...meta.defaults, name: "Новый агент" }));
  const [presets, setPresets] = useState<PresetInfo[]>([]);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [mode, setMode] = useState<"form" | "json">("form");
  const [json, setJson] = useState("");
  const [paramsText, setParamsText] = useState(() => JSON.stringify(cfg.model.params ?? {}, null, 2));
  const [questionsText, setQuestionsText] = useState(() => JSON.stringify(cfg.decision.questions ?? [], null, 2));
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => { api.presets().then(setPresets).catch(() => {}); }, []);
  useEffect(() => { api.symbols(cfg.data.provider).then(setSymbols).catch(() => setSymbols([])); }, [cfg.data.provider]);

  const set = (path: Path, value: unknown) => setCfg((c) => setIn(c, path, value));
  const numIn = (path: Path, value: number | null, props: Record<string, unknown> = {}) => (
    <input type="number" value={value ?? ""} {...props}
      onChange={(e) => set(path, e.target.value === "" ? null : Number(e.target.value))} />
  );

  const loadPreset = async (name: string) => {
    if (!name) return;
    try {
      const p = await api.preset(name);
      setCfg({ ...p, id: cfg.id });
      setParamsText(JSON.stringify(p.model.params ?? {}, null, 2));
      setQuestionsText(JSON.stringify(p.decision.questions ?? [], null, 2));
    } catch (e) { setErr((e as Error).message); }
  };

  const toJsonMode = () => {
    const merged = collect();
    if (!merged) return;
    setJson(JSON.stringify(merged, null, 2));
    setMode("json");
  };

  const toFormMode = () => {
    try {
      const c = JSON.parse(json) as AgentConfig;
      setCfg(c);
      setParamsText(JSON.stringify(c.model?.params ?? {}, null, 2));
      setQuestionsText(JSON.stringify(c.decision?.questions ?? [], null, 2));
      setMode("form");
    } catch (e) { setErr(`JSON: ${(e as Error).message}`); }
  };

  const collect = (): AgentConfig | null => {
    if (mode === "json") {
      try { return JSON.parse(json) as AgentConfig; } catch (e) { setErr(`JSON: ${(e as Error).message}`); return null; }
    }
    try {
      let out = setIn(cfg, ["model", "params"], JSON.parse(paramsText || "{}"));
      if (cfg.decision.schema_name === "custom") out = setIn(out, ["decision", "questions"], JSON.parse(questionsText || "[]"));
      return out;
    } catch (e) { setErr(`JSON в параметрах: ${(e as Error).message}`); return null; }
  };

  const save = async () => {
    setErr(null);
    const c = collect();
    if (!c) return;
    setSaving(true);
    try {
      if (initial) await api.update(initial.id, c); else await api.create(c);
      onSaved();
    } catch (e) { setErr((e as Error).message); }
    setSaving(false);
  };

  const p = cfg.model.provider;

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal form-modal" role="dialog" aria-label="Настройка агента">
        <header className="modal-head">
          <h2>{initial ? `Настройка: ${initial.name}` : "Новый агент"}</h2>
          <div className="seg">
            <button className={mode === "form" ? "on" : ""} onClick={() => mode === "json" && toFormMode()}>Форма</button>
            <button className={mode === "json" ? "on" : ""} onClick={() => mode === "form" && toJsonMode()}>JSON</button>
          </div>
          <button className="ghost" onClick={onClose} aria-label="Закрыть">✕</button>
        </header>

        <div className="modal-body">
          {mode === "json" ? (
            <textarea className="code" value={json} onChange={(e) => setJson(e.target.value)} spellCheck={false} rows={30} />
          ) : (
            <>
              {!initial && (
                <section>
                  <h4>Пресет</h4>
                  <select defaultValue="" onChange={(e) => loadPreset(e.target.value)}>
                    <option value="">— начать с пресета —</option>
                    {presets.map((pr) => (
                      <option key={pr.name} value={pr.name} disabled={!!pr.error}>{pr.title ?? pr.name} — {pr.description}</option>
                    ))}
                  </select>
                </section>
              )}

              <section className="grid2">
                <h4>Модель</h4>
                <label>Название<input value={cfg.name} onChange={(e) => set(["name"], e.target.value)} /></label>
                <label>Провайдер
                  <select value={p} onChange={(e) => {
                    const np = e.target.value as Provider;
                    setCfg((c) => setIn(setIn(c, ["model", "provider"], np), ["model", "model"], DEFAULT_MODEL[np] ?? ""));
                  }}>
                    {meta.model_providers.map((x) => <option key={x} value={x}>{PROVIDER_LABEL[x] ?? x}</option>)}
                  </select>
                </label>
                <label>Модель
                  {p === "baseline" ? (
                    <select value={cfg.model.model || "sma_cross"} onChange={(e) => set(["model", "model"], e.target.value)}>
                      {meta.baseline_strategies.map((s) => <option key={s}>{s}</option>)}
                    </select>
                  ) : p === "laya" ? (
                    <select value={cfg.model.model} onChange={(e) => set(["model", "model"], e.target.value)}>
                      {LAYA_MODELS.map((s) => <option key={s} value={s}>{s || "auto (router)"}</option>)}
                    </select>
                  ) : (
                    <>
                      <input value={cfg.model.model} list="model-suggest" onChange={(e) => set(["model", "model"], e.target.value)} />
                      <datalist id="model-suggest">{(p === "llm" ? LLM_SUGGESTIONS : [DEFAULT_MODEL[p] ?? ""]).map((s) => <option key={s} value={s} />)}</datalist>
                    </>
                  )}
                </label>
                {(p === "systemone" || p === "jev" || p === "llm") && (
                  <label>Base URL {p !== "systemone" && <small>(пусто = OpenRouter)</small>}
                    <input value={cfg.model.base_url ?? ""} placeholder={p === "systemone" ? "http://localhost:8001" : "https://openrouter.ai/api"}
                      onChange={(e) => set(["model", "base_url"], e.target.value || null)} />
                  </label>
                )}
                {LOCAL.includes(p) && (
                  <label>Цена часа вычислений, $<small>стоимость = время × ставка</small>
                    {numIn(["model", "cost_per_hour_usd"], cfg.model.cost_per_hour_usd, { step: 0.01, min: 0 })}
                  </label>
                )}
                {p !== "baseline" && (
                  <label>Формат состояния
                    <select value={cfg.model.state_format} onChange={(e) => set(["model", "state_format"], e.target.value)}>
                      <option value="text">текст</option><option value="json">JSON-объект</option>
                    </select>
                  </label>
                )}
                {p !== "baseline" && p !== "gliner" && (
                  <label>Env-переменная ключа <small>(пусто = OPENROUTER_API_KEY)</small>
                    <input value={cfg.model.api_key_env ?? ""} onChange={(e) => set(["model", "api_key_env"], e.target.value || null)} />
                  </label>
                )}
                <label className="wide">Параметры (JSON)
                  <textarea className="code" rows={3} value={paramsText} onChange={(e) => setParamsText(e.target.value)} spellCheck={false}
                    placeholder='{"temperature": 0, "max_tokens": 800}' />
                </label>
                {(p === "jev" || p === "llm") && !meta.openrouter_key_configured && (
                  <p className="warn wide">⚠ На сервере не задан OPENROUTER_API_KEY — добавьте его в .env</p>
                )}
              </section>

              <section className="grid2">
                <h4>Данные</h4>
                <label>Источник
                  <select value={cfg.data.provider} onChange={(e) => set(["data", "provider"], e.target.value)}>
                    {meta.data_providers.map((d) => <option key={d.name} value={d.name}>{d.name} — {d.description}</option>)}
                  </select>
                </label>
                <label>Символ
                  <input value={cfg.data.symbol} list="symbols" onChange={(e) => set(["data", "symbol"], e.target.value)} />
                  <datalist id="symbols">{symbols.map((s) => <option key={s} value={s} />)}</datalist>
                </label>
                <label>Интервал
                  <select value={cfg.data.interval} onChange={(e) => set(["data", "interval"], e.target.value)}>
                    {meta.intervals.map((i) => <option key={i}>{i}</option>)}
                  </select>
                </label>
                <label>Свечей максимум{numIn(["data", "limit"], cfg.data.limit, { min: 10, max: 20000 })}</label>
                <label>Начало (UTC)<input type="date" value={cfg.data.start?.slice(0, 10) ?? ""} onChange={(e) => set(["data", "start"], e.target.value || null)} /></label>
                <label>Конец (UTC)<input type="date" value={cfg.data.end?.slice(0, 10) ?? ""} onChange={(e) => set(["data", "end"], e.target.value || null)} /></label>
              </section>

              <section className="grid2">
                <h4>Что видит модель</h4>
                <label>Окно свечей{numIn(["features", "window"], cfg.features.window, { min: 2, max: 500 })}</label>
                <label>Индикаторы <small>({meta.indicators.join(", ")})</small>
                  <input value={cfg.features.indicators.join(", ")}
                    onChange={(e) => set(["features", "indicators"], e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
                </label>
                <label className="check wide">
                  <input type="checkbox" checked={cfg.features.anonymize} onChange={(e) => set(["features", "anonymize"], e.target.checked)} />
                  Анонимизировать (скрыть тикер и даты, цены от 100) — защита от «памяти» модели
                </label>
              </section>

              <section className="grid2">
                <h4>Вопросы к модели</h4>
                <label>Схема
                  <select value={cfg.decision.schema_name} onChange={(e) => set(["decision", "schema_name"], e.target.value)}>
                    {meta.decision_schemas.map((s) => <option key={s}>{s}</option>)}
                  </select>
                </label>
                {cfg.decision.schema_name === "default" ? (
                  <>
                    <label className="check"><input type="checkbox" checked={cfg.decision.sizing} onChange={(e) => set(["decision", "sizing"], e.target.checked)} />Спрашивать размер позиции</label>
                    <label className="check"><input type="checkbox" checked={cfg.decision.forecast} onChange={(e) => set(["decision", "forecast"], e.target.checked)} />Спрашивать прогноз направления</label>
                  </>
                ) : (
                  <label className="wide">Вопросы (JSON). role: action | size | forecast | null; варианты action ⊂ {Object.keys(meta.actions).join(", ")}
                    <textarea className="code" rows={8} value={questionsText} onChange={(e) => setQuestionsText(e.target.value)} spellCheck={false}
                      placeholder={'[{"id":"action","role":"action","instructions":"...","options":{"open_long":"...","hold":"..."}}]'} />
                  </label>
                )}
              </section>

              <section className="grid2">
                <h4>Брокер и прогон</h4>
                <label>Стартовый капитал{numIn(["broker", "initial_cash"], cfg.broker.initial_cash, { min: 1 })}</label>
                <label>Комиссия, %{numIn(["broker", "fee_pct"], cfg.broker.fee_pct, { step: 0.01, min: 0 })}</label>
                <label>Проскальзывание, %{numIn(["broker", "slippage_pct"], cfg.broker.slippage_pct, { step: 0.01, min: 0 })}</label>
                <label className="check"><input type="checkbox" checked={cfg.broker.allow_short} onChange={(e) => set(["broker", "allow_short"], e.target.checked)} />Разрешить шорт</label>
                <label>Решение каждые N свечей{numIn(["run", "decide_every"], cfg.run.decide_every, { min: 1 })}</label>
                <label>Пауза между шагами, мс{numIn(["run", "delay_ms"], cfg.run.delay_ms, { min: 0 })}</label>
                <label>Максимум решений <small>(пусто = все)</small>{numIn(["run", "max_steps"], cfg.run.max_steps, { min: 1 })}</label>
                <label>Горизонт прогноза, свечей{numIn(["run", "forecast_horizon"], cfg.run.forecast_horizon, { min: 1 })}</label>
                <label>Порог «flat», %{numIn(["run", "flat_threshold_pct"], cfg.run.flat_threshold_pct, { step: 0.05, min: 0 })}</label>
                <label>Стоп после N ошибок подряд <small>(0 = никогда)</small>{numIn(["run", "max_consecutive_errors"], cfg.run.max_consecutive_errors, { min: 0 })}</label>
              </section>
            </>
          )}
        </div>

        <footer className="modal-foot">
          {err && <span className="err">⚠ {err}</span>}
          <span className="spacer" />
          <button className="ghost" onClick={onClose}>Отмена</button>
          <button className="primary" onClick={save} disabled={saving}>{initial ? "Сохранить" : "Создать"}</button>
        </footer>
      </div>
    </div>
  );
}
