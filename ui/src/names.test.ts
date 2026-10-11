import { expect, it } from "vitest";
import { shortName } from "./names";

it("uses the first name, but keeps a title with the name after it", () => {
  expect(shortName("Marguerite Bell")).toBe("Marguerite");
  expect(shortName("Brother Anselm")).toBe("Brother Anselm");
  expect(shortName("Dr. Ada Lin")).toBe("Dr. Ada");
  expect(shortName("Rook")).toBe("Rook");
  expect(shortName("Brother")).toBe("Brother");
});
