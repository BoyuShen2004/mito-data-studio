export type CanvasViewport = {
  scrollLeft: number;
  scrollTop: number;
  clientWidth: number;
  clientHeight: number;
};

export type CanvasStage = {
  stageW: number;
  stageH: number;
  stageLeft: number;
  stageTop: number;
};

export function stageIntersectsViewport(
  viewport: CanvasViewport,
  stage: CanvasStage,
): boolean {
  if (
    viewport.clientWidth <= 0 ||
    viewport.clientHeight <= 0 ||
    stage.stageW <= 0 ||
    stage.stageH <= 0
  ) return false;
  const viewRight = viewport.scrollLeft + viewport.clientWidth;
  const viewBottom = viewport.scrollTop + viewport.clientHeight;
  return (
    stage.stageLeft + stage.stageW > viewport.scrollLeft &&
    stage.stageLeft < viewRight &&
    stage.stageTop + stage.stageH > viewport.scrollTop &&
    stage.stageTop < viewBottom
  );
}

