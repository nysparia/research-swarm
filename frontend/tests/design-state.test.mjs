import test from 'node:test';
import assert from 'node:assert/strict';
import {draftOverview, representativeAgents} from '../src/workbench/designState.ts';

test('draft cards reference existing source blocks and keep missing acceptance empty', () => {
  const blocks=[{id:'title',title:'课题',content:'# 课题'}, {id:'background',title:'研究背景与目的',content:'## 研究背景与目的\n\n真实用户目标'}, {id:'limit',title:'约束',content:'## 约束\n\n仅 CPU，保留负面结果'}];
  const rows=draftOverview(blocks,'课题');
  assert.deepEqual(rows.map(r=>r.blockIds),[['background'],['limit'],[]]);
  assert.equal(rows[0].content,'真实用户目标');
  assert.equal(rows[1].content,'仅 CPU，保留负面结果');
  assert.equal(rows[2].content,'');
  assert.equal(blocks[0].content,'# 课题');
});
test('avatars represent actual active nodes, favor running work, and never invent missing roles', () => {
  const n=(id,role,status,active=true)=>({id,role,title:role,status,active,finishedAt:null,startedAt:null});
  const rows=representativeAgents([n('old','实验','completed'),n('live','实验','running'),n('retired','论文检索','running',false),n('theory','假设论证','pending')]);
  assert.deepEqual(rows.map(r=>[r.node.id,r.role]),[['theory','理论'],['live','实验']]);
  assert.deepEqual(representativeAgents([]),[]);
});
test('draft summary recognizes a user heading added to a generic note', () => {
  const rows=draftOverview([{id:'added',title:'研究补充',content:'## 验收\n\n重复五次，报告方差'}],'课题');
  assert.equal(rows[2].content,'重复五次，报告方差');
  assert.deepEqual(rows[2].blockIds,['added']);
});
