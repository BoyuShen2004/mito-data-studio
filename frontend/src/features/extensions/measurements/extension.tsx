import ProjectMeasurements from "../../../components/ProjectMeasurements";
import type { ProjectExtensionContext } from "../types";

/** Adapter keeps the generic project host independent of measurement UI/API. */
export default function MeasurementsExtension({ volumes, isManager }: ProjectExtensionContext) {
  return <ProjectMeasurements volumes={volumes.data ?? []} loading={volumes.loading}
    error={volumes.error} canRun={isManager} onSaved={volumes.reload} />;
}
