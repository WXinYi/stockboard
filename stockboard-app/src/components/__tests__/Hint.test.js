// @vitest-environment happy-dom
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import Hint from '../Hint.vue'

describe('Hint.vue 说明气泡', () => {
  it('默认不显示正文, 点击 ? 后出现, 再点收起', async () => {
    const w = mount(Hint, { props: { text: '可买=直接打' } })
    expect(w.text()).not.toContain('可买=直接打')
    await w.find('.pk-hint-q').trigger('click')
    expect(w.text()).toContain('可买=直接打')
    expect(w.find('.pk-hint-pop').exists()).toBe(true)
    await w.find('.pk-hint-q').trigger('click')
    expect(w.find('.pk-hint-pop').exists()).toBe(false)
  })

  it('点击页面其它处自动收起', async () => {
    const w = mount(Hint, { props: { text: '只看不买=不入场' } })
    await w.find('.pk-hint-q').trigger('click')
    expect(w.find('.pk-hint-pop').exists()).toBe(true)
    await document.body.click()
    expect(w.find('.pk-hint-pop').exists()).toBe(false)
  })
})

it('按 Esc 收起', async () => {
  const w = mount(Hint, { props: { text: 'esc 关闭' }, attachTo: document.body })
  await w.find('.pk-hint-q').trigger('click')
  expect(w.find('.pk-hint-pop').exists()).toBe(true)
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
  await w.vm.$nextTick()
  expect(w.find('.pk-hint-pop').exists()).toBe(false)
  w.unmount()
})
