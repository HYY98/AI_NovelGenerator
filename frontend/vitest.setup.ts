import "@testing-library/jest-dom/vitest";

// React Aria 在 jsdom 中依赖这些浏览器观察器；测试只需稳定的空实现。
class ResizeObserverStub implements ResizeObserver {
  disconnect(): void {}
  observe(): void {}
  unobserve(): void {}
}

Object.defineProperty(globalThis, "ResizeObserver", {
  configurable: true,
  value: ResizeObserverStub,
});

Object.defineProperty(globalThis, "CSS", {
  configurable: true,
  value: { ...(globalThis.CSS ?? {}), escape: globalThis.CSS?.escape ?? ((value: string) => value) },
});
