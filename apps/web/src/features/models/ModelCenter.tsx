"use client";

import { useEffect, useMemo, useState } from "react";

import { studioApi } from "@/src/lib/api";
import type { useStudio } from "@/src/features/studio/useStudio";
import type {
  ModelConnection,
  ModelConnectionInput,
  ModelDiscovery,
  ModelProvider,
  ModelProtocol,
  ModelSpeedResult,
} from "@/src/types/studio";

type Props = {
  studio: ReturnType<typeof useStudio>;
  workspaceId: string;
  canManage: boolean;
};

const protocols: Record<ModelProtocol, string> = {
  openai: "OpenAI-compatible",
  anthropic: "Anthropic Messages",
  gemini: "Gemini generateContent",
  azure_openai: "Azure OpenAI v1",
};

const initial = (provider?: ModelProvider): ModelConnectionInput => ({
  provider: provider?.id ?? "openai",
  name: provider?.name ?? "OpenAI",
  protocol: provider?.protocol ?? "openai",
  base_url: provider?.base_url ?? "https://api.openai.com/v1",
  api_key: "",
  model_id: "",
  is_default: false,
});

const fromConnection = (item: ModelConnection): ModelConnectionInput => ({
  provider: item.provider,
  name: item.name,
  protocol: item.protocol,
  base_url: item.base_url,
  api_key: "",
  model_id: item.model_id,
  is_default: item.is_default,
});

const errorText = (error: unknown) =>
  error instanceof Error ? error.message : "操作未完成，请重试";

export function ModelCenter({ studio, workspaceId, canManage }: Props) {
  const connections = studio.modelConnections;
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<ModelConnectionInput>(initial());
  const [providerSearch, setProviderSearch] = useState("");
  const [modelSearch, setModelSearch] = useState("");
  const [discovery, setDiscovery] = useState<ModelDiscovery | null>(null);
  const [result, setResult] = useState<ModelSpeedResult | null>(null);
  const [notice, setNotice] = useState<{ bad: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<"save" | "models" | "test" | "delete" | null>(null);

  const current = connections.find((item) => item.id === selectedId);
  const defaultConnection = connections.find((item) => item.is_default);
  const usableDefault = Boolean(defaultConnection?.model_id);
  const filteredProviders = useMemo(
    () =>
      providers.filter((item) =>
        (item.name + " " + item.id + " " + item.group)
          .toLowerCase()
          .includes(providerSearch.trim().toLowerCase()),
      ),
    [providers, providerSearch],
  );
  const filteredModels = useMemo(
    () =>
      (discovery?.models ?? []).filter((item) =>
        (item.id + " " + item.name).toLowerCase().includes(modelSearch.trim().toLowerCase()),
      ),
    [discovery, modelSearch],
  );

  useEffect(() => {
    let mounted = true;
    studioApi
      .listModelProviders()
      .then((data) => {
        if (mounted) setProviders(data);
      })
      .catch((error: unknown) => {
        if (mounted) setNotice({ bad: true, text: errorText(error) });
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (selectedId !== null || connections.length === 0) return;
    const first = connections[0];
    const pending = window.setTimeout(() => {
      setSelectedId(first.id);
      setForm(fromConnection(first));
    }, 0);
    return () => window.clearTimeout(pending);
  }, [connections, selectedId]);

  function setField<K extends keyof ModelConnectionInput>(key: K, value: ModelConnectionInput[K]) {
    setForm((previous) => ({ ...previous, [key]: value }));
    setResult(null);
  }

  function chooseProvider(provider: ModelProvider) {
    if (!canManage) return;
    setSelectedId("new");
    setForm(initial(provider));
    setDiscovery(null);
    setResult(null);
    setNotice(null);
  }

  function chooseConnection(item: ModelConnection) {
    setSelectedId(item.id);
    setForm(fromConnection(item));
    setDiscovery(null);
    setResult(null);
    setNotice(null);
  }

  async function save() {
    if (!canManage) return;
    setBusy("save");
    setNotice(null);
    try {
      const payload: ModelConnectionInput = {
        ...form,
        name: form.name.trim(),
        base_url: form.base_url.trim(),
        api_key: form.api_key.trim(),
        model_id: form.model_id.trim(),
      };
      if (!payload.name || !payload.base_url) {
        setNotice({ bad: true, text: "请填写连接名称和 API Base URL" });
        return;
      }
      const saved = current
        ? await studioApi.updateModelConnection(
            current.id,
            {
              name: payload.name,
              base_url: payload.base_url,
              model_id: payload.model_id,
              is_default: payload.is_default,
              ...(payload.api_key ? { api_key: payload.api_key } : {}),
              ...(payload.provider === "custom" ? { protocol: payload.protocol } : {}),
            },
            workspaceId,
          )
        : await studioApi.createModelConnection(payload, workspaceId);
      setSelectedId(saved.id);
      setForm(fromConnection(saved));
      setNotice({
        bad: false,
        text: current
          ? "配置已保存。建议重新测试连接，确认新参数可用。"
          : "连接已创建。获取模型列表、选择模型后再测试推理能力。",
      });
      await studio.refresh();
    } catch (error) {
      setNotice({ bad: true, text: errorText(error) });
    } finally {
      setBusy(null);
    }
  }

  async function discover() {
    if (!current || !canManage) return;
    setBusy("models");
    setNotice(null);
    setModelSearch("");
    try {
      const response = await studioApi.discoverModels(current.id, workspaceId);
      setDiscovery(response);
      setNotice({ bad: false, text: response.notice });
    } catch (error) {
      setDiscovery(null);
      setNotice({ bad: true, text: errorText(error) });
    } finally {
      setBusy(null);
    }
  }

  async function test(samples: number) {
    if (!current || !canManage) return;
    if (!form.model_id.trim()) {
      setNotice({ bad: true, text: "请先选择或填写模型 ID 后再测试。" });
      return;
    }
    if (
      form.api_key ||
      form.base_url.trim() !== current.base_url ||
      form.model_id.trim() !== current.model_id
    ) {
      setNotice({
        bad: true,
        text: "模型 ID / Base URL / API Key 有未保存的更改。请先保存后再测试。",
      });
      return;
    }
    setBusy("test");
    setResult(null);
    setNotice(null);
    try {
      const response = await studioApi.testModelConnection(
        current.id,
        form.model_id.trim(),
        samples,
        workspaceId,
      );
      setResult(response);
      setNotice({
        bad: false,
        text: "真实推理请求成功。供应商可能对测试请求计费；测速数值不等同于模型质量。",
      });
      await studio.refresh();
    } catch (error) {
      setNotice({ bad: true, text: errorText(error) });
      await studio.refresh();
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    if (!current || !canManage) return;
    if (!window.confirm("确定删除此模型连接？使用它的未完成任务可能因此失败，密钥密文也会删除。"))
      return;
    setBusy("delete");
    setNotice(null);
    try {
      await studioApi.deleteModelConnection(current.id, workspaceId);
      setSelectedId(null);
      setDiscovery(null);
      setResult(null);
      await studio.refresh();
      setNotice({ bad: false, text: "连接已删除；如删除默认模型，系统会自动选择另一条连接。" });
    } catch (error) {
      setNotice({ bad: true, text: errorText(error) });
    } finally {
      setBusy(null);
    }
  }

  const isNew = !current;
  const currentPreset = providers.find((item) => item.id === form.provider);

  return (
    <div className="model-center">
      <div className="model-titlebar">
        <div>
          <div className="eyebrow">MODEL GATEWAY · WORKSPACE SETTINGS</div>
          <h1>
            模型连接 <span className="model-title-quiet">/ Model Hub</span>
          </h1>
          <p>在同一个工作空间管理多个大模型供应商。配置、发现、测试，最后设为 Agent 默认模型。</p>
        </div>
        {canManage && (
          <button
            className="btn primary"
            type="button"
            onClick={() => {
              chooseProvider(
                providers.find((item) => item.id === "openai") ?? {
                  id: "openai",
                  name: "OpenAI",
                  protocol: "openai",
                  base_url: "https://api.openai.com/v1",
                  group: "国际",
                },
              );
            }}
          >
            ＋ 新增模型连接
          </button>
        )}
      </div>

      <div className="model-stats">
        <div>
          <span>已配置连接</span>
          <strong>{connections.length}</strong>
          <small>工作空间共享</small>
        </div>
        <div>
          <span>当前默认模型</span>
          <strong className="model-stat-model">
            {usableDefault ? defaultConnection?.model_id : "尚未指定"}
          </strong>
          <small>{defaultConnection?.name ?? "需先建立连接"}</small>
        </div>
        <div>
          <span>已验证连接</span>
          <strong>{connections.filter((item) => item.last_status === "ok").length}</strong>
          <small>实际推理成功</small>
        </div>
        <div>
          <span>支持供应商入口</span>
          <strong>{providers.length}</strong>
          <small>含本地、云端与自定义</small>
        </div>
      </div>

      {!canManage && (
        <p className="model-permission">
          当前账户可以查看工作空间的连接状态。只有 owner / admin
          可以管理密钥、发现模型与发起可能计费的测试。
        </p>
      )}

      <div className="model-columns">
        <aside className="model-rail">
          <div className="model-card-heading">
            <strong>已添加的连接</strong>
            <span>{connections.length} 条</span>
          </div>
          <div className="model-connection-list">
            {connections.length === 0 && (
              <p className="model-empty">还没有连接。可从右侧选择供应商开始配置。</p>
            )}
            {connections.map((item) => (
              <button
                key={item.id}
                type="button"
                className={"model-connection-item" + (selectedId === item.id ? " selected" : "")}
                onClick={() => chooseConnection(item)}
              >
                <span className="model-avatar">{item.name.slice(0, 1).toUpperCase()}</span>
                <span className="model-connection-text">
                  <strong>{item.name}</strong>
                  <small>{item.model_id || "待选择模型"}</small>
                </span>
                <span className="model-connection-flags">
                  {item.is_default && <em className="model-default-tag">默认</em>}
                  <i className={"model-status-dot" + (item.last_status === "ok" ? " good" : "")} />
                </span>
              </button>
            ))}
          </div>
          <div className="model-catalog-rail">
            <div className="model-card-heading">
              <strong>供应商目录</strong>
              <span>官方 / 自定义</span>
            </div>
            <input
              value={providerSearch}
              onChange={(event) => setProviderSearch(event.target.value)}
              placeholder="搜索供应商..."
              aria-label="搜索模型供应商"
            />
            <div className="model-preset-grid">
              {filteredProviders.map((provider) => (
                <button
                  type="button"
                  key={provider.id}
                  title={provider.name}
                  disabled={!canManage}
                  onClick={() => chooseProvider(provider)}
                >
                  <span className="model-preset-mark">
                    {provider.name.slice(0, 1).toUpperCase()}
                  </span>
                  <span>{provider.name}</span>
                </button>
              ))}
            </div>
          </div>
        </aside>

        <section className="model-editor">
          <div className="model-panel-head">
            <div>
              <span className="eyebrow">CONNECTION CONFIGURATION</span>
              <h2>{isNew ? "添加供应商连接" : current?.name || "模型连接详情"}</h2>
              <p>Base URL 与密钥只提交到本地后端；密钥不可从界面读回。</p>
            </div>
            <span className="model-protocol-badge">{protocols[form.protocol]}</span>
          </div>

          <div className="model-form-grid">
            <label className="model-field">
              <span>
                供应商 <b>*</b>
              </span>
              <select
                value={form.provider}
                disabled={!isNew || !canManage || busy !== null}
                onChange={(event) => {
                  const preset = providers.find((item) => item.id === event.target.value);
                  if (preset) chooseProvider(preset);
                }}
              >
                {providers.map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.name} · {item.group}
                  </option>
                ))}
              </select>
            </label>
            <label className="model-field">
              <span>
                连接名称 <b>*</b>
              </span>
              <input
                value={form.name}
                disabled={!canManage || busy !== null}
                maxLength={80}
                onChange={(event) => setField("name", event.target.value)}
                placeholder="给这条连接命名"
              />
            </label>
            <label className="model-field model-field-wide">
              <span>
                API Base URL <b>*</b>
              </span>
              <input
                value={form.base_url}
                disabled={!canManage || busy !== null}
                onChange={(event) => setField("base_url", event.target.value)}
                placeholder={
                  form.provider === "azure"
                    ? "https://YOUR-RESOURCE.openai.azure.com/openai/v1"
                    : "https://provider.example.com/v1"
                }
                spellCheck={false}
              />
              <small>
                公网仅允许 HTTPS；本地自托管模型可使用 localhost / 127.0.0.1 的非特权 HTTP
                端口（1024–65535）。
              </small>
            </label>
            {form.provider === "custom" && (
              <label className="model-field model-field-wide">
                <span>连接协议</span>
                <select
                  disabled={!canManage || busy !== null}
                  value={form.protocol}
                  onChange={(event) => setField("protocol", event.target.value as ModelProtocol)}
                >
                  {Object.entries(protocols).map(([key, name]) => (
                    <option key={key} value={key}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label className="model-field model-field-wide">
              <span>
                API Key / 访问凭据{" "}
                {form.provider !== "ollama" && form.provider !== "lmstudio" && <b>*</b>}
              </span>
              <input
                type="password"
                autoComplete="new-password"
                value={form.api_key}
                disabled={!canManage || busy !== null}
                onChange={(event) => setField("api_key", event.target.value)}
                placeholder={
                  current?.has_api_key ? "已加密保存；留空则保持原密钥" : "输入供应商 API Key"
                }
              />
              <small>
                {current?.has_api_key
                  ? "✓ 密钥已加密存储在本地；输入新值可轮换，旧密钥不会显示。"
                  : "凭据不会回传浏览器；本地开发密钥依赖后端的 .local 主密钥文件。"}
              </small>
            </label>
            <label className="model-field model-field-wide">
              <span>默认调用模型 / Deployment ID</span>
              <input
                value={form.model_id}
                disabled={!canManage || busy !== null}
                onChange={(event) => setField("model_id", event.target.value)}
                placeholder="可从下方获取模型，也可手动粘贴模型 ID"
                spellCheck={false}
              />
              {form.provider === "azure" && (
                <small>
                  Azure 必须输入实际部署名称（Deployment Name），并使用资源的 /openai/v1 地址。
                </small>
              )}
            </label>
          </div>

          <label className="model-default-toggle">
            <input
              type="checkbox"
              checked={form.is_default}
              disabled={!canManage || busy !== null || Boolean(current?.is_default)}
              onChange={(event) => setField("is_default", event.target.checked)}
            />
            <span>
              <strong>设为工作空间默认模型</strong>
              <small>Agent 创建与新建 Run 优先使用此连接。每个工作空间最多一条默认连接。</small>
            </span>
          </label>
          <div className="model-edit-actions">
            {current && canManage && (
              <button
                type="button"
                className="btn model-danger"
                disabled={busy !== null}
                onClick={() => void remove()}
              >
                删除连接
              </button>
            )}
            <span />
            {canManage && (
              <button
                type="button"
                className="btn primary"
                disabled={busy !== null}
                onClick={() => void save()}
              >
                {busy === "save" ? "保存中…" : isNew ? "保存连接" : "保存更改"}
              </button>
            )}
          </div>

          {notice && (
            <div role="status" className={"model-message" + (notice.bad ? " bad" : "")}>
              {notice.text}
            </div>
          )}

          <div className="model-divider" />

          <div className="model-action-head">
            <div>
              <h3>发现可用模型</h3>
              <p>从供应商接口实时拉取，不能保证所有列出的模型均有推理权限。</p>
            </div>
            <button
              type="button"
              className="btn secondary"
              disabled={!current || !canManage || busy !== null}
              onClick={() => void discover()}
            >
              {busy === "models" ? "获取中…" : "↻ 获取模型列表"}
            </button>
          </div>

          {discovery && (
            <div className="model-discovery">
              <div className="model-discovery-head">
                <span>
                  {discovery.source === "provider_api" ? "来自供应商 API" : "仅支持手动输入"}
                </span>
                <strong>{discovery.models.length} 个模型</strong>
              </div>
              {discovery.models.length > 8 && (
                <input
                  aria-label="筛选模型列表"
                  value={modelSearch}
                  placeholder="输入模型名称或 ID 进行筛选"
                  onChange={(event) => setModelSearch(event.target.value)}
                />
              )}
              <div className="model-discovery-list">
                {filteredModels.length === 0 && (
                  <p className="model-empty">
                    当前接口没有返回可选择的文本模型，请按供应商文档手动填写模型 ID。
                  </p>
                )}
                {filteredModels.slice(0, 100).map((model) => (
                  <div className="model-discovery-row" key={model.id}>
                    <div>
                      <strong>{model.name}</strong>
                      <small>{model.id}</small>
                    </div>
                    <button
                      type="button"
                      disabled={!canManage || busy !== null}
                      className={
                        "model-select-button" + (form.model_id === model.id ? " chosen" : "")
                      }
                      onClick={() => setField("model_id", model.id)}
                    >
                      {form.model_id === model.id ? "已选择" : "选择"}
                    </button>
                  </div>
                ))}
              </div>
              {filteredModels.length > 100 && (
                <small className="model-mini-hint">仅显示前 100 条，请进一步搜索模型 ID。</small>
              )}
            </div>
          )}

          <div className="model-divider" />

          <div className="model-action-head">
            <div>
              <h3>连接验证与响应测速</h3>
              <p>向指定模型发送真实、极短的测试请求；可能消耗 Token 与额度。</p>
            </div>
            <span className="model-test-chip">LIVE API · 非模拟</span>
          </div>
          <div className="model-test-actions">
            <button
              type="button"
              className="btn secondary"
              disabled={!current || !canManage || busy !== null}
              onClick={() => void test(1)}
            >
              {busy === "test" ? "测试中…" : "◉ 测试连接 · 1 次"}
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={!current || !canManage || busy !== null}
              onClick={() => void test(3)}
            >
              {busy === "test" ? "测速中…" : "↗ 运行速率测试 · 3 次"}
            </button>
          </div>
          {result && (
            <div className="model-results" aria-live="polite">
              <div className="model-result-head">
                <strong>✓ 真实推理调用成功</strong>
                <span>
                  {result.model_id} · {result.samples} 次样本
                </span>
              </div>
              <div className="model-metrics">
                <div>
                  <small>平均端到端延迟</small>
                  <strong>
                    {result.latency_ms.toLocaleString()}
                    <em>ms</em>
                  </strong>
                </div>
                <div>
                  <small>有效输出速率</small>
                  <strong>
                    {result.effective_tokens_per_second === null
                      ? "—"
                      : result.effective_tokens_per_second}
                    <em> tok/s</em>
                  </strong>
                </div>
                <div>
                  <small>总输出 Token</small>
                  <strong>{result.output_tokens === null ? "—" : result.output_tokens}</strong>
                </div>
              </div>
              {result.samples > 1 && (
                <small>
                  单次延迟范围：{result.min_latency_ms} – {result.max_latency_ms} ms
                </small>
              )}
              <p>
                {result.metric_note}{" "}
                {result.usage_reported
                  ? ""
                  : "当前供应商没有返回可核验的 token usage，因此不显示推测速率。"}
              </p>
            </div>
          )}
          {!result && current?.last_tested_at && (
            <p className="model-last-test">
              上次测试：
              {current.last_status === "ok" ? "成功" : "失败（" + current.last_status + "）"}
              {current.last_latency_ms !== null ? " · " + current.last_latency_ms + " ms" : ""}
              {current.last_tokens_per_second !== null
                ? " · " + current.last_tokens_per_second + " tok/s"
                : ""}
            </p>
          )}
          <div className="model-bottom-tip">
            <span>🔒</span>
            <p>
              工作空间管理员可使用保存的密钥。速率只反映当前网络和供应商响应，不代表并发能力、官方限流、首
              Token 延迟或稳定
              SLA；文本对话测试不代表工具调用能力已验证。当前仍是本地开发版，禁止将工作台对公网开放。
            </p>
          </div>
        </section>
      </div>
      {currentPreset && (
        <p className="model-footnote">
          当前协议：{protocols[currentPreset.protocol]}。供应商 API
          可能调整端点或支持范围，获取模型列表及测试结果以当前实际响应为准。
        </p>
      )}
    </div>
  );
}
