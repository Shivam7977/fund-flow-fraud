/**
 * FastAPI errors 2 tarah se aate hain:
 * - HTTPException(400, "message") → { detail: "message" }   (string)
 * - Pydantic validation fail → { detail: [{ msg: "...", loc: [...], ... }, ...] }  (array)
 * Isse handle na kiya jaye to array wale case mein "[object Object]" dikhta hai.
 */
function getErrorMessage(data) {
  if (!data || !data.detail) return "Kuch galat ho gaya, dobara try karo";
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) {
    return data.detail.map((d) => d.msg || JSON.stringify(d)).join(" · ");
  }
  return "Kuch galat ho gaya, dobara try karo";
}