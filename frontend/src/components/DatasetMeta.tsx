import type { DatasetMetadata } from "../types/project";
import MetadataCard from "./MetadataCard";

/** One entry point for biomedical dataset metadata on every role surface. */
export default function DatasetMeta({ metadata, title = "Dataset metadata", hideWhenEmpty = false }: {
  metadata?: DatasetMetadata | null;
  title?: string;
  hideWhenEmpty?: boolean;
}) {
  return <MetadataCard metadata={metadata} title={title} hideWhenEmpty={hideWhenEmpty}/>;
}
