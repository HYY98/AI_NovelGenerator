const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";

/** 普通 JSON 请求可选配置。 */
export interface ApiRequestOptions {
  headers?: HeadersInit;
  signal?: AbortSignal;
}

/** GET SSE 请求可选配置。 */
export interface ApiGetSSEOptions extends ApiRequestOptions {
  lastEventId?: string | null;
}

/** 已解析的 SSE 消息。 */
export interface ApiSseMessage<T = Record<string, unknown>> {
  id: string | null;
  event: string;
  data: T;
}

/** SSE 消息处理器。 */
export type SSEEventHandler<T = Record<string, unknown>> = (
  message: ApiSseMessage<T>
) => void | Promise<void>;

interface SSEParserOptions {
  initialLastEventId: string | null;
  ignoreInvalidJson: boolean;
}

/** 后端字段级校验错误，键使用请求字段路径，值为该字段的可读消息列表。 */
export type ApiFieldErrors = Record<string, string[]>;

/**
 * 功能：表示保留 HTTP 与领域错误上下文的统一 API 异常。
 * Args:
 *   constructorOptions: 状态码、错误码、消息、字段错误和原始响应体。
 * Returns:
 *   可按原生 Error 使用，并可读取 status、code、fieldErrors 与 body 的异常实例。
 */
export class ApiRequestError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly fieldErrors: ApiFieldErrors;
  readonly body: unknown;

  constructor({
    status,
    code,
    message,
    fieldErrors,
    body,
  }: {
    status: number;
    code?: string | null;
    message: string;
    fieldErrors?: ApiFieldErrors;
    body?: unknown;
  }) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code ?? null;
    this.fieldErrors = fieldErrors ?? {};
    this.body = body;
  }
}

/**
 * 功能：判断未知值是否为可按键读取的普通对象。
 * Args:
 *   value: 待判断的未知值。
 * Returns:
 *   值为非空且非数组对象时返回 true。
 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * 功能：合并默认请求头与调用方请求头，调用方同名值优先。
 * Args:
 *   defaults: 当前请求类型的默认请求头。
 *   customHeaders: 调用方传入的可选请求头。
 * Returns:
 *   可直接交给 fetch 的 Headers 实例。
 */
function mergeHeaders(defaults: HeadersInit, customHeaders?: HeadersInit): Headers {
  const headers = new Headers(defaults);
  if (customHeaders) {
    new Headers(customHeaders).forEach((value, key) => headers.set(key, value));
  }
  return headers;
}

/**
 * 功能：从后端错误响应中提取稳定、可读的错误消息。
 * Args:
 *   body: 已解析的 JSON 或原始文本响应体。
 *   status: HTTP 状态码。
 * Returns:
 *   优先取 detail 对象的 message，其次取 detail、顶层 message 或默认消息。
 */
function getErrorMessage(body: unknown, status: number): string {
  if (isRecord(body)) {
    const detail = body.detail;
    if (isRecord(detail) && typeof detail.message === "string" && detail.message) {
      return detail.message;
    }
    if (typeof detail === "string" && detail) {
      return detail;
    }
    if (Array.isArray(detail)) {
      const validationMessages = detail
        .map((item) => {
          if (!isRecord(item)) return null;
          if (typeof item.message === "string") return item.message;
          if (typeof item.msg === "string") return item.msg;
          return null;
        })
        .filter((message): message is string => Boolean(message));
      if (validationMessages.length > 0) {
        return validationMessages.join("；");
      }
    }
    if (typeof body.message === "string" && body.message) {
      return body.message;
    }
  }
  if (typeof body === "string" && body.trim()) {
    return body.trim();
  }
  return `Request failed: ${status}`;
}

/**
 * 功能：从后端错误体提取稳定的领域错误码。
 * Args:
 *   body: 已解析的 JSON 或原始文本错误体。
 * Returns:
 *   detail.code 或顶层 code；均不存在时返回 null。
 */
function getErrorCode(body: unknown): string | null {
  if (!isRecord(body)) return null;
  const detail = body.detail;
  if (isRecord(detail) && typeof detail.code === "string" && detail.code) {
    return detail.code;
  }
  return typeof body.code === "string" && body.code ? body.code : null;
}

/**
 * 功能：把 FastAPI 与领域错误中的字段问题归一化为字段路径映射。
 * Args:
 *   body: 已解析的 JSON 或原始文本错误体。
 * Returns:
 *   以点号字段路径为键、消息数组为值的字段错误表。
 */
function getFieldErrors(body: unknown): ApiFieldErrors {
  if (!isRecord(body)) return {};
  const result: ApiFieldErrors = {};
  const detail = body.detail;

  const append = (field: string, message: string) => {
    if (!field || !message) return;
    result[field] = [...(result[field] ?? []), message];
  };

  // FastAPI 422 使用 detail 数组，并通过 loc 表达 body 中的字段路径。
  if (Array.isArray(detail)) {
    for (const item of detail) {
      if (!isRecord(item)) continue;
      const loc = Array.isArray(item.loc)
        ? item.loc.filter((part) => part !== "body").map(String).join(".")
        : "";
      const message =
        typeof item.message === "string"
          ? item.message
          : typeof item.msg === "string"
            ? item.msg
            : "";
      append(loc, message);
    }
  }

  // 领域层既可返回 field_errors，也可直接返回 fieldErrors；两者统一兼容。
  const domainDetail = isRecord(detail) ? detail : body;
  const rawFieldErrors = domainDetail.field_errors ?? domainDetail.fieldErrors;
  if (isRecord(rawFieldErrors)) {
    for (const [field, messages] of Object.entries(rawFieldErrors)) {
      if (typeof messages === "string") append(field, messages);
      if (Array.isArray(messages)) {
        for (const message of messages) {
          if (typeof message === "string") append(field, message);
        }
      }
    }
  }
  return result;
}

/**
 * 功能：校验 fetch 响应并将后端错误体转换为 Error。
 * Args:
 *   response: fetch 返回的 HTTP 响应。
 * Returns:
 *   响应成功时完成；失败时抛出包含后端消息的 Error。
 */
async function assertResponseOk(response: Response): Promise<void> {
  if (response.ok) return;

  const rawBody = await response.text().catch(() => "");
  let body: unknown = rawBody;
  if (rawBody) {
    try {
      body = JSON.parse(rawBody) as unknown;
    } catch {
      // 非 JSON 错误响应保留原始文本，便于诊断代理层或服务进程错误。
    }
  }
  throw new ApiRequestError({
    status: response.status,
    code: getErrorCode(body),
    message: getErrorMessage(body, response.status),
    fieldErrors: getFieldErrors(body),
    body,
  });
}

/**
 * 功能：在 SSE 文本缓冲区中定位下一处完整换行符。
 * Args:
 *   buffer: 尚未消费的 SSE 文本。
 *   streamEnded: 数据流是否已经结束。
 * Returns:
 *   换行起点和长度；当前尚无完整换行时返回 null。
 */
function findLineEnding(
  buffer: string,
  streamEnded: boolean
): { index: number; length: number } | null {
  for (let index = 0; index < buffer.length; index += 1) {
    const character = buffer[index];
    if (character === "\n") {
      return { index, length: 1 };
    }
    if (character === "\r") {
      if (index + 1 < buffer.length) {
        return {
          index,
          length: buffer[index + 1] === "\n" ? 2 : 1,
        };
      }
      return streamEnded ? { index, length: 1 } : null;
    }
  }
  return null;
}

/**
 * 功能：流式读取 SSE，解析 id、event 以及由多行 data 拼接的 JSON 数据。
 * Args:
 *   response: 已通过状态校验的 SSE 响应。
 *   onEvent: 每条完整 SSE 消息的处理器。
 *   options: 初始事件游标及非法 JSON 处理策略。
 * Returns:
 *   响应流完整消费后结束。
 */
async function consumeSSE<T>(
  response: Response,
  onEvent: SSEEventHandler<T>,
  options: SSEParserOptions
): Promise<void> {
  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("SSE 响应没有可读取的响应体");
  }

  const decoder = new TextDecoder();
  let buffer = "";
  let eventType = "message";
  let dataLines: string[] = [];
  let lastEventId = options.initialLastEventId;

  /**
   * 功能：派发当前已完成的 SSE 消息并清空事件级缓冲。
   * Args:
   *   无。
   * Returns:
   *   当前事件处理器执行完成后结束。
   */
  const dispatchEvent = async (): Promise<void> => {
    const currentEventType = eventType || "message";
    const rawData = dataLines.join("\n");
    eventType = "message";
    dataLines = [];

    // 仅有 id、event 或注释的帧用于更新状态，不产生业务回调。
    if (!rawData) return;

    let parsedData: T;
    try {
      parsedData = JSON.parse(rawData) as T;
    } catch {
      if (options.ignoreInvalidJson) return;
      throw new Error(`SSE 事件 ${currentEventType} 的 data 不是合法 JSON`);
    }
    await onEvent({
      id: lastEventId,
      event: currentEventType,
      data: parsedData,
    });
  };

  /**
   * 功能：按 SSE 字段规则消费单行文本。
   * Args:
   *   line: 已去除换行符的单行文本。
   * Returns:
   *   字段更新或空行派发完成后结束。
   */
  const consumeLine = async (line: string): Promise<void> => {
    if (line === "") {
      await dispatchEvent();
      return;
    }
    if (line.startsWith(":")) return;

    const separatorIndex = line.indexOf(":");
    const field = separatorIndex === -1 ? line : line.slice(0, separatorIndex);
    let value = separatorIndex === -1 ? "" : line.slice(separatorIndex + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    if (field === "event") {
      eventType = value;
    } else if (field === "data") {
      dataLines.push(value);
    } else if (field === "id" && !value.includes("\0")) {
      // SSE 规范要求 id 跨事件保存，供断线后的 Last-Event-ID 使用。
      lastEventId = value;
    }
  };

  /**
   * 功能：从当前缓冲区连续取出所有完整 SSE 行。
   * Args:
   *   streamEnded: 数据流是否已经结束。
   * Returns:
   *   当前所有完整行消费完成后结束。
   */
  const drainBuffer = async (streamEnded: boolean): Promise<void> => {
    let lineEnding = findLineEnding(buffer, streamEnded);
    while (lineEnding) {
      const line = buffer.slice(0, lineEnding.index);
      buffer = buffer.slice(lineEnding.index + lineEnding.length);
      await consumeLine(line);
      lineEnding = findLineEnding(buffer, streamEnded);
    }
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      await drainBuffer(false);
    }

    buffer += decoder.decode();
    await drainBuffer(true);
    if (buffer) {
      await consumeLine(buffer);
      buffer = "";
    }
    // 兼容服务端未以空行收尾但已经发送完整 data 的情况。
    await dispatchEvent();
  } finally {
    reader.releaseLock();
  }
}

/**
 * 功能：把相对图片路径转换为后端可访问的完整 URL。
 * Args:
 *   url: 图片相对路径、完整 URL、data URL 或空值。
 * Returns:
 *   可直接用于图片组件的 URL；空值返回空字符串。
 */
export function getImageUrl(url: string | null | undefined): string {
  if (!url) return "";
  if (url.startsWith("http") || url.startsWith("data:")) return url;
  return `${API_BASE}${url}`;
}

/**
 * 功能：发送 JSON GET 请求。
 * Args:
 *   path: 后端 API 相对路径。
 *   options: 可选请求头和中止信号。
 * Returns:
 *   解析后的 JSON 响应。
 */
export async function apiGet<T = unknown>(
  path: string,
  options: ApiRequestOptions = {}
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "GET",
    headers: mergeHeaders({ "Content-Type": "application/json" }, options.headers),
    signal: options.signal,
  });
  await assertResponseOk(response);
  return response.json() as Promise<T>;
}

/**
 * 功能：发送 JSON PUT 请求。
 * Args:
 *   path: 后端 API 相对路径。
 *   data: 将被 JSON 序列化的请求体。
 *   options: 可选请求头和中止信号。
 * Returns:
 *   解析后的 JSON 响应。
 */
export async function apiPut<T = unknown>(
  path: string,
  data: unknown,
  options: ApiRequestOptions = {}
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "PUT",
    headers: mergeHeaders({ "Content-Type": "application/json" }, options.headers),
    body: JSON.stringify(data),
    signal: options.signal,
  });
  await assertResponseOk(response);
  return response.json() as Promise<T>;
}

/**
 * 功能：发送 JSON POST 请求。
 * Args:
 *   path: 后端 API 相对路径。
 *   data: 将被 JSON 序列化的请求体。
 *   options: 可选请求头和中止信号。
 * Returns:
 *   解析后的 JSON 响应。
 */
export async function apiPost<T = unknown>(
  path: string,
  data: unknown,
  options: ApiRequestOptions = {}
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: mergeHeaders({ "Content-Type": "application/json" }, options.headers),
    body: JSON.stringify(data),
    signal: options.signal,
  });
  await assertResponseOk(response);
  return response.json() as Promise<T>;
}

/**
 * 功能：发送 multipart/form-data POST 请求。
 * Args:
 *   path: 后端 API 相对路径。
 *   formData: 浏览器构造的表单数据。
 * Returns:
 *   解析后的 JSON 响应。
 */
export async function apiPostForm<T = unknown>(
  path: string,
  formData: FormData
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    body: formData,
  });
  await assertResponseOk(response);
  return response.json() as Promise<T>;
}

/**
 * 功能：发送 JSON DELETE 请求。
 * Args:
 *   path: 后端 API 相对路径。
 *   options: 可选请求头和中止信号。
 * Returns:
 *   解析后的 JSON 响应。
 */
export async function apiDelete<T = unknown>(
  path: string,
  options: ApiRequestOptions = {}
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "DELETE",
    headers: mergeHeaders({ "Content-Type": "application/json" }, options.headers),
    signal: options.signal,
  });
  await assertResponseOk(response);
  return response.json() as Promise<T>;
}

/**
 * 功能：发送 GET SSE 请求并流式处理支持断线续传的 JSON 事件。
 * Args:
 *   path: 后端 SSE 相对路径。
 *   onEvent: 接收 id、event 与解析后 data 的消息处理器。
 *   options: 可选请求头、中止信号与 Last-Event-ID 游标。
 * Returns:
 *   SSE 响应流正常结束后完成。
 */
export async function apiGetSSE<T = Record<string, unknown>>(
  path: string,
  onEvent: SSEEventHandler<T>,
  options: ApiGetSSEOptions = {}
): Promise<void> {
  const headers = mergeHeaders({ Accept: "text/event-stream" }, options.headers);
  if (options.lastEventId !== undefined && options.lastEventId !== null) {
    headers.set("Last-Event-ID", options.lastEventId);
  }

  const response = await fetch(`${API_BASE}${path}`, {
    method: "GET",
    headers,
    signal: options.signal,
  });
  await assertResponseOk(response);
  await consumeSSE(response, onEvent, {
    initialLastEventId: options.lastEventId ?? null,
    ignoreInvalidJson: false,
  });
}

/**
 * 功能：发送带 JSON 请求体的 SSE POST 请求并逐条回调事件。
 * Args:
 *   path: 后端 SSE 相对路径。
 *   data: 将被 JSON 序列化的请求体。
 *   onEvent: 接收 event 与解析后 data 的消息处理器。
 *   options: 可选请求头和中止信号。
 * Returns:
 *   SSE 响应流正常结束后完成。
 */
export async function apiPostSSE(
  path: string,
  data: unknown,
  onEvent: (event: string, data: Record<string, unknown>) => void,
  options: ApiRequestOptions = {}
): Promise<void> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: mergeHeaders(
      { Accept: "text/event-stream", "Content-Type": "application/json" },
      options.headers
    ),
    body: JSON.stringify(data),
    signal: options.signal,
  });
  await assertResponseOk(response);
  await consumeSSE<Record<string, unknown>>(
    response,
    ({ event, data: eventData }) => onEvent(event, eventData),
    {
      initialLastEventId: null,
      // 兼容旧接口：格式异常的服务端事件继续忽略，不改变既有调用行为。
      ignoreInvalidJson: true,
    }
  );
}
