export function latestManagerReview(managerReview?: string | null): string {
  const text = (managerReview ?? "").trim();
  if (!text) {
    return "";
  }
  const parts = text
    .split(/\n\s*\n/g)
    .map((part) => part.trim())
    .filter(Boolean);
  return parts.at(-1) ?? text;
}
