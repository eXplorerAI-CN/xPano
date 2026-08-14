import type { Matrix4 } from '../../lib/contracts.ts'

function multiply(left: Matrix4, right: Matrix4): Matrix4 {
  const result = new Array<number>(16).fill(0)
  for (let row = 0; row < 4; row += 1) {
    for (let column = 0; column < 4; column += 1) {
      result[row * 4 + column] = (
        left[row * 4] * right[column]
        + left[row * 4 + 1] * right[4 + column]
        + left[row * 4 + 2] * right[8 + column]
        + left[row * 4 + 3] * right[12 + column]
      )
    }
  }
  return result as unknown as Matrix4
}

export function composeWorldFromThreePreview(
  current: Matrix4,
  rotationThree: number[],
  pivotThree: [number, number, number],
): Matrix4 {
  const signs = [1, -1, -1]
  const rotationColmap = new Array<number>(9).fill(0)
  for (let row = 0; row < 3; row += 1) {
    for (let column = 0; column < 3; column += 1) {
      rotationColmap[row * 3 + column] = signs[row] * rotationThree[row * 3 + column] * signs[column]
    }
  }
  const pivot: [number, number, number] = [pivotThree[0], -pivotThree[1], -pivotThree[2]]
  const translation = pivot.map((value, row) => value - (
    rotationColmap[row * 3] * pivot[0]
    + rotationColmap[row * 3 + 1] * pivot[1]
    + rotationColmap[row * 3 + 2] * pivot[2]
  ))
  const incremental: Matrix4 = [
    rotationColmap[0], rotationColmap[1], rotationColmap[2], translation[0],
    rotationColmap[3], rotationColmap[4], rotationColmap[5], translation[1],
    rotationColmap[6], rotationColmap[7], rotationColmap[8], translation[2],
    0, 0, 0, 1,
  ]
  return multiply(incremental, current)
}
