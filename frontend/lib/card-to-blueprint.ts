import { Card, CardBlueprint, BlueprintInputSchema, BlueprintOutputSchema } from "@/lib/types";

export function cardToBlueprintPreview(card: Card, projectId: string): CardBlueprint {
  const now = new Date().toISOString();
  const skills = Array.isArray(card.executor_context?.skills) ? (card.executor_context?.skills as string[]) : [];
  const mcpServers = Array.isArray(card.executor_context?.mcp_servers)
    ? (card.executor_context?.mcp_servers as string[])
    : [];

  const instructions: string[] = [];
  if (card.summary) instructions.push(card.summary);
  if (card.why) instructions.push(card.why);
  if (card.key_findings.length) instructions.push(...card.key_findings);
  if (card.next_actions.length) instructions.push(...card.next_actions);

  const inputsSchema: BlueprintInputSchema[] = card.inputs.map((input, index) => ({
    slot: `input_${index + 1}`,
    label: input.label,
    accepted_formats: [],
    required: true,
    description: input.asset_id ? `资产: ${input.asset_id}` : null,
  }));

  const outputsSchema: BlueprintOutputSchema[] = card.outputs.map((output, index) => ({
    role: `output_${index + 1}`,
    label: output.label,
    artifact_class: output.artifact_class ?? "figure",
    accepted_formats: output.accepted_formats ?? [],
    preferred_format: output.preferred_format ?? null,
    required: output.required ?? true,
    description: output.asset_id ? `资产: ${output.asset_id}` : null,
  }));

  return {
    blueprint_id: `preview-${card.card_id}`,
    version: "1.0.0",
    schema_version: "card_library.v1",
    title: card.title,
    summary: card.summary || card.why || "等待执行",
    tags: [],
    domain: "",
    cover_art: null,
    skills,
    mcp_servers: mcpServers,
    runtime_requirements: {
      python: "__system__",
      r: "__system__",
    },
    inputs_schema: inputsSchema,
    outputs_schema: outputsSchema,
    parameters: [],
    instruction_blocks: instructions,
    provenance: {
      source_card_id: card.card_id,
      source_project_id: projectId,
      created_at: now,
      created_by: "user",
      last_used_at: null,
      use_count: 0,
    },
  };
}
