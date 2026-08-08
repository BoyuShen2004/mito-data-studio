/// <reference lib="webworker" />

import { planLocalInterpolation } from "./localInterpolate";

type Request = {
  firstPlane: Int32Array;
  lastPlane: Int32Array;
  h: number;
  w: number;
  firstIndex: number;
  lastIndex: number;
  label: number;
};

self.onmessage = (event: MessageEvent<Request>) => {
  const request = event.data;
  const slices = planLocalInterpolation(
    request.firstPlane,
    request.lastPlane,
    request.h,
    request.w,
    request.firstIndex,
    request.lastIndex,
    request.label,
  );
  self.postMessage(slices, { transfer: slices.map((slice) => slice.mask.buffer) });
};
