"""Browser-only UX checks. Synthetic QA data never reaches a real research server.
Run after npm run build. Requires Python playwright + Chromium.
Usage: python scripts/check_macos_ui.py --dist dist --output /tmp/sw-ui-check
"""
import argparse, copy, json, threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright

NOW = '2026-10-03T09:30:00+08:00'
DOC = '# 小样本条件下的检索增强生成\n\n## 研究问题\n在标注数据有限时，检索增强生成能否改善回答的可靠性？\n\n## 研究边界\n只比较公开数据集上的可复现实验。区分检索质量与生成质量，不将相关性直接解释为因果关系。\n\n## 验证方式\n固定模型与提示词，比较有检索和无检索两组。记录引用准确率、回答正确率和失败案例。\n\n## 交付内容\n形成文献综述、实验方案与可追溯的研究记录。'
REPORT = '# 小样本条件下的检索增强生成\n\n本轮完成了研究边界与验证方案的整理。以下内容为界面测试资料，不代表真实研究结论。\n\n## 当前判断\n检索是否有帮助，需要在固定模型、提示词与数据划分的条件下验证。不能仅凭回答更详细，就认定准确率有所提升。\n\n## 建议的验证路径\n首先建立不含检索的基线，再控制检索数量与文档质量，分别比较引用准确率和回答正确率。\n\n## 局限与下一步\n当前测试资料不含实测数据。完成实验后，应记录失败案例、方差和适用边界，再判断主张是否成立。'

def node(id, title, step, status, parent='central'):
    return dict(id=id, parentId=parent, title=title, role=title, kind='research', phase='execute', status=status, progress=0, input={'researchStep': step}, output={'summary': '界面测试记录：待核对实际研究资料。', 'evidenceIds': []}, logs=[{'id':id+'-log', 'at':NOW, 'actor':'system', 'message':'正在核对研究边界与来源。'}], sourceNodeId=None, requirementIds=[], evidenceIds=[], startedAt=NOW, finishedAt=None, elapsedMs=0, version=1, active=True)

def make_detail(id, phase):
    task = {'id':id, 'title':{'qa-prep':'小样本条件下的检索增强生成','qa-research':'检索质量如何影响回答可靠性','qa-output':'检索增强生成的验证方案'}[id], 'phase':phase, 'updatedAt':NOW, 'round':1}
    nodes = [node('central','研究协调员','synthesis','running',None), node('background','明确研究边界','background','completed'), node('literature','文献与证据核对','literature','running'), node('design','设计对照实验','experiment_design','pending')]
    nodes[-1]['input']['experimentProtocol'] = {'dataset':'公开问答数据集', 'metrics':['引用准确率'], 'baselines':['无检索基线'], 'replicates':3}
    if phase=='completed':
        for n in nodes:n.update(status='completed',finishedAt=NOW)
    artifacts = [{'id':'state:'+n['id'], 'kind':'node_state', 'title':n['title'], 'revision':1, 'status':n['status'], 'nodeIds':[n['id']], 'evidenceIds':[], 'claimRefs':[], 'content':{'summary':n['output']['summary']}} for n in nodes]
    artifacts.append({'id':'report:live', 'kind':'report', 'title':'研究报告', 'revision':1, 'status':'draft', 'nodeIds':[], 'evidenceIds':[], 'claimRefs':[], 'content':{'markdown':REPORT}})
    state = {'revision':1, 'paused':False, 'project':{'title':task['title'], 'researchDecision':None}, 'nodes':nodes, 'edges':[], 'papers':[], 'facetNodes':[], 'evidence':[], 'checkpoints':[], 'activeCheckpointId':None, 'requirements':[], 'activity':[], 'report':{'summary':REPORT, 'claims':[], 'ready':True, 'approved':False}, 'claimGraph':{'schemaVersion':1, 'claims':[], 'relations':[], 'expressions':[], 'materials':[]}}
    return {'task':task, 'taskMode':'research', 'phase':phase, 'document':{'markdown':DOC, 'revision':1, 'polishing':False, 'polishedFrom':1, 'source':'local', 'questions':[], 'error':None}, 'messages':[{'id':'m1', 'role':'user', 'content':'我想研究小样本条件下，检索增强生成能否改善回答的可靠性。', 'at':NOW}, {'id':'m2', 'role':'assistant', 'content':'我们可以先把问题拆成两个部分：**检索是否找到了合适的证据**，以及**生成是否忠实于这些证据**。\n\n我已整理研究范围和验证方式，你可以在右侧直接修改。确认后，再开始文献核对与实验设计。', 'at':NOW}], 'state':None if phase=='requirements' else state, 'error':None, 'artifacts':[], 'runs':[], 'modelReady':True, 'interactions':[], 'workbench':{'artifacts':artifacts,'draft':{'revision':1,'blocks':[{'id':'goal','title':'研究目标','content':DOC,'kind':'section','source':'user','locked':True}]},'report':{'markdown':REPORT,'ready':phase=='completed','approved':False}, 'plan':{'capabilities':[]}, 'executionSettings':{'resources':{'cpuCores':1,'gpuCount':0}}}}

class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args): pass


def run(dist, output):
    output.mkdir(parents=True,exist_ok=True)
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(QuietHandler,directory=str(dist.resolve())))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url=f'http://127.0.0.1:{server.server_port}'
    fixtures={key:make_detail(key,phase) for key,phase in [('qa-prep','requirements'),('qa-research','researching'),('qa-output','completed')]}
    requests=[]; errors=[]; checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':1440,'height':960},device_scale_factor=1,locale='zh-CN')
        page=context.new_page()
        page.on('pageerror',lambda e: errors.append(str(e)))
        def route(route):
            req=route.request; path=req.url.split('/api',1)[1]; requests.append({'method':req.method,'path':path,'body':req.post_data_json if req.method=='POST' else None})
            data={}
            if path=='/settings': data={'mode':'llm','providers':{},'capabilities':{'modelReady':False},'sourcePath':''}
            elif path=='/tasks': data={'tasks':[d['task'] for d in fixtures.values()]}
            else:
                bits=path.strip('/').split('/'); detail=fixtures.get(bits[1] if len(bits)>1 else '')
                if detail and len(bits)==2: data=detail
                elif detail and path.endswith('/document'):
                    body=req.post_data_json; detail['document'].update(markdown=body['markdown'],revision=detail['document']['revision']+1); data=detail
                elif detail and path.endswith('/messages'):
                    body=req.post_data_json; detail['messages'].append({'id':'new-'+str(len(requests)),'role':'user','content':body['text'],'at':NOW});data=detail
                elif detail and '/actions/' in path:
                    detail['state']['paused']=path.endswith('/pause');data=detail
                elif path.endswith('/materials'):data=[]
                elif detail and path.endswith('/execution-settings'):data=detail['workbench']['executionSettings']
                elif path.endswith('/export'):
                    route.fulfill(status=200,content_type='application/zip',body=b'QA export');return
                elif detail and path.endswith('/interactions'):
                    body=req.post_data_json;data={'id':'qa-interaction','kind':body['kind'],'text':body['text'],'status':'completed','target':body['target'],'reply':'测试回复','showInConversation':True}
                else:
                    route.fulfill(status=404,json={'error':'QA endpoint not implemented: '+path});return
            route.fulfill(status=200,json=data)
        page.route('**/api/**',route)
        def check(name,condition):
            assert condition,name
            checks.append(name)
        def crop(name, locator, pad=5):
            box=locator.bounding_box();assert box,name
            page.screenshot(path=str(output/(name+'.png')),clip={'x':max(0,box['x']-pad),'y':max(0,box['y']-pad),'width':box['width']+pad*2,'height':box['height']+pad*2},animations='disabled')
        def shot(name):
            page.wait_for_timeout(800)
            check(name+' / no horizontal overflow',page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
            check(name+' / app rendered',page.locator('.sw-fatal').count()==0)
            check(name+' / no workflow strip',page.locator('.sw-stage-progress').count()==0)
            page.screenshot(path=str(output/(name+'.png')),full_page=True,animations='disabled')
        page.goto(url); page.wait_for_timeout(1500)
        if not page.get_by_role('heading',name='你想研究什么？').count():
            page.screenshot(path=str(output/'failure.png')); print('APP ERRORS', errors); print(page.locator('body').inner_text())
        page.get_by_role('heading',name='你想研究什么？').wait_for(); shot('01-entry')
        page.get_by_role('button',name='探索一个问题',exact=True).click()
        check('workflow strip removed',page.locator('.sw-stage-progress').count()==0)
        check('entry suggestion populates composer', '我想研究的问题' in page.get_by_role('textbox',name='与研究助手对话').input_value())
        page.wait_for_function('document.activeElement?.getAttribute("aria-label") === "与研究助手对话"')
        check('suggestion focuses composer',page.get_by_role('textbox',name='与研究助手对话').evaluate('(el)=>el===document.activeElement'))
        page.get_by_role('button',name='收起侧边栏').click();page.wait_for_timeout(400)
        check('closed sidebar inert',page.locator('.sw-sidebar').get_attribute('inert')=='')
        page.get_by_role('button',name='展开侧边栏').click();page.wait_for_timeout(400)
        page.get_by_role('button',name='搜索任务',exact=True).click();page.get_by_role('textbox',name='搜索科研任务').fill('不存在')
        check('task search empty state',page.get_by_text('没有匹配的任务').is_visible())
        page.get_by_role('textbox',name='搜索科研任务').fill('');page.get_by_role('button',name='搜索任务',exact=True).click()
        page.get_by_role('button',name='调整外观',exact=True).click();page.get_by_label('玻璃染色').focus();page.get_by_label('玻璃染色').press('End')
        check('glass tint responds',page.get_by_label('玻璃染色').input_value()=='100')
        check('glass tint applied to workspace',float(page.locator('.sw-macos').evaluate('(el)=>getComputedStyle(el).getPropertyValue("--sw-glass-alpha")'))>.89)
        page.get_by_role('switch').check()
        check('reduce transparency applied',page.locator('.sw-macos').get_attribute('data-reduce-transparency')=='true')
        check('reduce transparency removes blur',page.locator('.sw-sidebar').evaluate('(el)=>getComputedStyle(el).backdropFilter')=='none')
        page.get_by_role('switch').uncheck();page.get_by_label('玻璃染色').focus();page.get_by_label('玻璃染色').press('Home')
        for _ in range(13):page.get_by_label('玻璃染色').press('ArrowRight')
        shot('10-appearance')
        check('Mac kit regular switch geometry',page.get_by_role('switch').bounding_box()['width']==54 and page.get_by_role('switch').bounding_box()['height']==24)
        crop('control-switch',page.get_by_role('switch'))
        page.get_by_label('玻璃染色').press('Escape');page.wait_for_timeout(300)
        check('appearance Escape returns focus',page.get_by_role('button',name='调整外观').evaluate('(el)=>el===document.activeElement'))
        page.goto(url+'/#task/qa-prep');page.locator('.cm-content').wait_for();shot('02-preparation')
        check('Mac kit large primary geometry',page.locator('.sw-prep-document>footer .sw-button-primary').bounding_box()['height']==28)
        crop('control-button',page.locator('.sw-prep-document>footer .sw-button-primary'))
        check('unified sidebar row rhythm',page.locator('.sw-task').first.bounding_box()['height']==36)
        divider=page.get_by_role('separator',name='调整对话与工作区宽度')
        divider.focus();divider.press('ArrowRight');check('keyboard split resize',divider.get_attribute('aria-valuenow')=='44')
        divider.press('Enter');check('keyboard split reset',divider.get_attribute('aria-valuenow')=='42')
        before_width=page.locator('.sw-prep-conversation').bounding_box()['width'];box=divider.bounding_box()
        page.mouse.move(box['x'],box['y']+100);page.mouse.down();page.mouse.move(box['x']+60,box['y']+100,steps=8);page.mouse.up()
        check('pointer split resize',page.locator('.sw-prep-conversation').bounding_box()['width']>before_width+40)
        divider.dblclick();check('double click split reset',divider.get_attribute('aria-valuenow')=='42')
        page.keyboard.press('Control+k');page.wait_for_function('document.activeElement?.getAttribute("aria-label") === "与研究助手对话"');check('desktop composer shortcut',page.get_by_role('textbox',name='与研究助手对话').evaluate('(el)=>el===document.activeElement'))
        page.get_by_role('button',name='预览',exact=True).click();check('Markdown preview',page.locator('.sw-md-preview').is_visible())
        page.get_by_role('button',name='编辑',exact=True).click();check('Markdown editor returns',page.locator('.cm-content').is_visible())
        page.locator('.cm-content').click();page.keyboard.press('Control+End');page.keyboard.insert_text('\n\n## 验证补充\n保留失败案例。');page.wait_for_timeout(1500)
        check('document edits persist through API', '保留失败案例。' in fixtures['qa-prep']['document']['markdown'])
        check('document saves with revision guard',any(r['method']=='POST' and r['path'].endswith('/document') and r['body'].get('expectedRevision')==1 for r in requests))
        textarea=page.get_by_role('textbox',name='与研究助手对话');textarea.fill('这是保留在当前任务的草稿')
        before=len([r for r in requests if r['method']=='POST'])
        textarea.dispatch_event('compositionstart');textarea.press('Enter');textarea.dispatch_event('compositionend')
        check('IME Enter does not submit',len([r for r in requests if r['method']=='POST'])==before)
        textarea.press('Shift+Enter');check('Shift Enter keeps draft',textarea.input_value().startswith('这是保留'))
        textarea.fill('长段落输入测试\n' * 12);page.wait_for_timeout(160)
        check('floating composer reserves its real height',page.locator('.sw-dialogue-scroll').evaluate('(el)=>parseFloat(getComputedStyle(el).paddingBottom)') >= page.locator('.sw-dialogue-compose').bounding_box()['height']-1)
        textarea.fill('请补充失败案例');textarea.press('Enter');page.wait_for_timeout(300)
        check('Enter submits once',len([r for r in requests if r['method']=='POST' and r['path'].endswith('/messages')])==1)
        page.goto(url+'/#task/qa-research');page.locator('.sa-agent-card').first.wait_for();shot('03-research')
        check('actual fixture nodes present',page.locator('.sa-agent-card').count()==4)
        check('Over-glass XL segment geometry',page.get_by_role('group',name='工作区视图').bounding_box()['height']==36)
        crop('control-segments',page.locator('.sw-kit-segments'))
        page.get_by_role('group',name='工作区视图').get_by_role('button',name='研究过程').focus();page.keyboard.press('ArrowRight')
        check('segmented keyboard navigation',page.locator('.sw-outputs').is_visible())
        page.keyboard.press('ArrowLeft');check('segmented keyboard return',page.locator('.sa-structure').is_visible())
        transform=page.locator('.sa-world').get_attribute('style');page.locator('.sa-viewport').focus();page.locator('.sa-viewport').press('ArrowRight')
        check('graph keyboard navigation',page.locator('.sa-world').get_attribute('style')!=transform)
        page.locator('[data-agent-id="central"] .sa-agent-heading').click();page.locator('.ant-drawer-open').wait_for()
        check('node inspector opens',page.locator('.ant-drawer-open').get_by_text('引用证据',exact=True).is_visible())
        page.locator('.ant-drawer-open .ant-drawer-close').click();page.wait_for_timeout(300)
        page.get_by_role('button',name='暂停',exact=True).click();page.get_by_role('button',name='继续',exact=True).wait_for();check('pause updates live control',fixtures['qa-research']['state']['paused'])
        page.get_by_role('button',name='继续',exact=True).click()
        page.locator('[data-agent-id="central"]').get_by_role('button',name='从这里深入：研究协调员').click()
        check('node reference retained',page.locator('.sw-composer-context').is_visible())
        page.get_by_role('button',name='取消节点引用').click()
        page.get_by_role('button',name='研究成果',exact=True).click();check('workspace segmented switch',page.locator('.sw-outputs').is_visible())
        page.get_by_role('button',name='研究过程',exact=True).first.click();check('structure restored',page.locator('.sa-structure').is_visible())
        page.goto(url+'/#task/qa-output');page.locator('.sw-output-paper').wait_for();shot('04-outputs')
        check('output selectors share one glass family',page.locator('.sw-visual-workspace .sw-glass-segments').count()==2)
        page.get_by_role('button',name='继续讨论',exact=True).click();check('moved discussion action retains context',page.locator('.sw-composer-context').is_visible());page.get_by_role('button',name='取消节点引用').click()
        page.get_by_role('button',name='实验材料',exact=True).click();check('honest empty files',page.get_by_text('本轮还没有交付文件').is_visible())
        page.get_by_role('button',name='研究报告',exact=True).click()
        with page.expect_download() as download:page.get_by_role('button',name='导出',exact=True).click()
        check('export download wired',download.value.suggested_filename=='科研任务-第1轮.zip')
        page.get_by_role('button',name='任务菜单').click();menuitem=page.get_by_role('menuitem',name='历史与检查点');menuitem.hover();page.wait_for_timeout(300)
        check('Mac kit blue menu hover',menuitem.evaluate('(el)=>getComputedStyle(el).backgroundColor')=='rgb(0, 106, 255)')
        shot('12-task-menu');crop('control-menu',page.locator('.ant-dropdown-menu:visible'))
        page.locator('.sw-title-stack').click()
        page.get_by_role('button',name='模型与设置',exact=False).click();page.get_by_role('dialog').wait_for();shot('05-settings')
        check('Mac kit regular field geometry',page.locator('#deepseek-key').bounding_box()['height']==24)
        page.locator('#deepseek-key').focus();crop('control-field',page.locator('#deepseek-key'))
        page.get_by_role('dialog').get_by_role('button',name='关闭').click() if page.get_by_role('dialog').get_by_role('button',name='关闭').count() else page.keyboard.press('Escape')
        page.set_viewport_size({'width':390,'height':844});page.goto(url);page.get_by_role('heading',name='你想研究什么？').wait_for();shot('06-mobile-entry')
        page.get_by_role('button',name='展开侧边栏').click();page.wait_for_timeout(350);page.keyboard.press('Escape');page.wait_for_timeout(350)
        check('mobile Escape closes navigation',page.locator('.sw-sidebar').get_attribute('inert')=='')
        for id,name in [('qa-prep','07-mobile-document'),('qa-research','08-mobile-research'),('qa-output','09-mobile-output')]:
            page.goto(url+'/#task/'+id);page.get_by_role('button',name='工作区',exact=True).wait_for();page.get_by_role('button',name='工作区',exact=True).click();shot(name)
        page.set_viewport_size({'width':320,'height':740});page.goto(url+'/#task/qa-research');page.locator('.sw-dialogue').wait_for();shot('11-compact-conversation')
        check('compact toolbar controls stay visible',page.locator('.sw-shell-actions').bounding_box()['x']+page.locator('.sw-shell-actions').bounding_box()['width']<=320)
        check('compact layout hides resize handle',not page.get_by_role('separator',name='调整对话与工作区宽度').is_visible())
        page.emulate_media(reduced_motion='reduce');page.goto(url);page.get_by_role('heading',name='你想研究什么？').wait_for()
        check('reduced motion honored',page.locator('.sw-entry-welcome').evaluate('(el)=>parseFloat(getComputedStyle(el).animationDuration)<.01'))
        check('no uncaught browser errors',not errors)
        browser.close()
    server.shutdown()
    result={'checks':checks,'pageErrors':errors,'total':len(checks),'fixtureNote':'All research content is synthetic, isolated browser QA data; no live model or scientific result validated.'}
    (output/'checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--dist',type=Path,default=Path('dist'));parser.add_argument('--output',type=Path,default=Path('/tmp/sw-ui-check'));args=parser.parse_args();run(args.dist,args.output)
