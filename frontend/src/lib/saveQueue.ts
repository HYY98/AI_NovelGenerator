"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiRequestError } from "@/lib/api";

/** 保存队列状态（技术指引 24 节）。 */
export type SaveState = "clean" | "dirty" | "saving" | "saved" | "conflict" | "error";

export const SAVE_STATE_LABEL: Record<SaveState, string> = {
  clean: "",
  dirty: "待保存…",
  saving: "保存中…",
  saved: "已保存",
  conflict: "版本冲突",
  error: "保存失败",
};

export interface SaveQueueOptions<T extends object> {
  /** 执行一次保存，返回服务端最新版本号。 */
  onSave: (patch: Partial<T>, baseVersion: number) => Promise<number>;
  onConflict: (message: string) => void;
  onError: (message: string) => void;
  onSaved?: (newVersion: number) => void;
  debounceMs?: number;
}

export interface SaveQueue<T extends object> {
  state: SaveState;
  lastSavedAt: Date | null;
  /** 是否还有未提交的改动。 */
  hasPending: () => boolean;
  /** 记录当前基线版本（切换实体或重新加载后调用）。 */
  setBaseVersion: (version: number) => void;
  /** 累积一次改动并触发防抖保存。 */
  schedule: (patch: Partial<T>) => void;
  /** 立即保存全部待提交改动；返回是否已全部落库。 */
  flush: () => Promise<boolean>;
  /** 清空队列（切换实体时使用）。 */
  reset: () => void;
}

const FLUSH_POLL_MS = 20;
const FLUSH_MAX_WAIT_MS = 15000;

/**
 * 功能：提供带状态机与 flush 能力的防抖保存队列。
 *
 * 关键行为：
 * 1. 连续改动累积为同一个 patch，不会互相覆盖；
 * 2. 保存期间产生的新改动会在本次结束后立刻补一次保存；
 * 3. 保存失败把本次改动放回队列，由调用方提示并可重试；
 * 4. flush() 供切换章节、点击定稿等场景强制落库。
 *
 * Args:
 *   options: 保存回调与通知回调。
 * Returns:
 *   队列状态与操作方法。
 */
export function useSaveQueue<T extends object>(options: SaveQueueOptions<T>): SaveQueue<T> {
  const { onSave, onConflict, onError, onSaved, debounceMs = 700 } = options;
  const [state, setState] = useState<SaveState>("clean");
  const [lastSavedAt, setLastSavedAt] = useState<Date | null>(null);

  const pendingRef = useRef<Partial<T>>({});
  const versionRef = useRef<number>(1);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const runningRef = useRef(false);
  const handlersRef = useRef({ onSave, onConflict, onError, onSaved });
  handlersRef.current = { onSave, onConflict, onError, onSaved };

  const runSave = useCallback(async (): Promise<boolean> => {
    if (runningRef.current) return false;
    const patch = { ...pendingRef.current };
    if (!Object.keys(patch).length) {
      setState("clean");
      return true;
    }
    runningRef.current = true;
    pendingRef.current = {};
    setState("saving");
    let succeeded = false;
    try {
      const nextVersion = await handlersRef.current.onSave(patch, versionRef.current);
      versionRef.current = nextVersion;
      handlersRef.current.onSaved?.(nextVersion);
      setLastSavedAt(new Date());
      succeeded = true;
      setState(Object.keys(pendingRef.current).length ? "dirty" : "saved");
    } catch (error) {
      // 失败时把本次变更放回队列，绝不静默丢弃用户输入
      pendingRef.current = { ...patch, ...pendingRef.current };
      const message = error instanceof Error ? error.message : "保存失败";
      const isConflict = error instanceof ApiRequestError && error.status === 409;
      setState(isConflict ? "conflict" : "error");
      if (isConflict) handlersRef.current.onConflict(message);
      else handlersRef.current.onError(message);
    } finally {
      runningRef.current = false;
    }
    // 保存过程中又产生了新改动：立刻补一次，保证顺序落库
    if (succeeded && Object.keys(pendingRef.current).length) void runSave();
    return succeeded;
  }, []);

  const schedule = useCallback(
    (patch: Partial<T>) => {
      pendingRef.current = { ...pendingRef.current, ...patch };
      setState("dirty");
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        void runSave();
      }, debounceMs);
    },
    [runSave, debounceMs]
  );

  const flush = useCallback(async (): Promise<boolean> => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    // 等待正在进行的保存结束，避免并发写同一章节
    const deadline = Date.now() + FLUSH_MAX_WAIT_MS;
    while (runningRef.current && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, FLUSH_POLL_MS));
    }
    if (!Object.keys(pendingRef.current).length) return true;
    return runSave();
  }, [runSave]);

  const reset = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    pendingRef.current = {};
    setState("clean");
  }, []);

  const setBaseVersion = useCallback((version: number) => {
    versionRef.current = version;
  }, []);

  const hasPending = useCallback(() => Object.keys(pendingRef.current).length > 0, []);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  // 存在未保存内容时拦截刷新，避免静默丢失编辑
  useEffect(() => {
    if (state !== "dirty" && state !== "saving") return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [state]);

  return { state, lastSavedAt, hasPending, setBaseVersion, schedule, flush, reset };
}
