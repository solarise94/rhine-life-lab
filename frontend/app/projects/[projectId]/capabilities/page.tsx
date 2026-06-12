"use client";

import { useParams } from "next/navigation";
import { CapabilitiesPanel } from "@/components/capabilities/CapabilitiesPanel";

export default function CapabilitiesPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = params?.projectId ?? "";

  return <CapabilitiesPanel projectId={projectId} />;
}
