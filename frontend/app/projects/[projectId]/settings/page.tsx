import { ProjectWorkspace } from "@/components/layout/ProjectWorkspace";

export default async function SettingsPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  return <ProjectWorkspace projectId={projectId} view="settings" />;
}
