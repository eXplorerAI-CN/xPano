import assert from 'node:assert/strict'
import test from 'node:test'

import { brandAbout } from '../src/app/brandAbout.ts'

test('brand about content names the project, publisher, and contributors', () => {
  assert.equal(brandAbout.productName, 'xPano')
  assert.equal(brandAbout.publisher, '由知天下开源')
  assert.equal(brandAbout.contributors, '感谢邵青，beluga，霞霞和其他开源项目的贡献者。')
  assert.equal(brandAbout.thirdPartyContributors, '感谢 FFmpeg、LichtFeld Studio（LFS）和其他开源项目的贡献。')
  assert.equal(brandAbout.license, 'xPano 源码遵循 MIT 协议。第三方组件遵循其随附许可。')
})
