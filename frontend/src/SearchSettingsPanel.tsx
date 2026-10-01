import { Button, Checkbox, Divider, Input, InputNumber, Select, Space } from 'antd';
import type { SearchKeySource, SearchSettings, Settings } from './types';

const numericFields: { key: keyof SearchSettings; label: string; min: number; max: number }[] = [
  { key: 'queryCount', label: '每次最多查询组数', min: 1, max: 24 },
  { key: 'perQuery', label: '每个源每组召回上限', min: 1, max: 100 },
  { key: 'candidateLimit', label: '每次去重后候选池上限', min: 10, max: 1000 },
  { key: 'maxRequests', label: '每次 API 请求上限（含重试）', min: 3, max: 200 },
  { key: 'maxSeconds', label: '每次检索时间上限（秒）', min: 10, max: 600 },
  { key: 'maxCalls', label: '自主研究每轮检索调用上限', min: 1, max: 40 },
  { key: 'seedCount', label: '引用追踪种子论文数', min: 0, max: 10 },
  { key: 'neighborsPerSeed', label: '每篇种子每个方向扩展上限', min: 1, max: 50 },
  { key: 'yearFrom', label: '近期查询起始年份（经典查询不限年份）', min: 1900, max: 2100 },
];

export function SearchSettingsPanel({ value, onChange, settings, keys, onKeyChange, disabled }: {
  value: SearchSettings; onChange: (value: SearchSettings) => void; settings: Settings | null;
  keys: Partial<Record<SearchKeySource, string | null>>;
  onKeyChange: (source: SearchKeySource, value: string | null) => void; disabled: boolean;
}) {
  return <section aria-label="论文检索设置">
    <Divider>论文检索深度与广度</Divider>
    <p className="quiet-text">每次检索扩展多组查询，合并候选并追踪一层引用。候选论文全部入库，模型每次最多收到 10 篇优先阅读列表；更多候选可继续在库内检索。引用关系不表示支持结论。</p>
    <label className="simple-label" htmlFor="search-profile">检索档位</label>
    <Select id="search-profile" style={{ width: '100%' }} value={value.profile} disabled={disabled} options={[{ value: 'standard', label: '标准 · 150 篇候选 / 120 秒' }, { value: 'deep', label: '深入 · 500 篇候选 / 300 秒' }]} onChange={profile => onChange({ ...value, ...settings?.searchProfiles?.[profile], profile })} />
    <label className="simple-label">主动检索来源</label>
    <Checkbox.Group value={value.sources} disabled={disabled} options={[{ label: 'OpenAlex', value: 'openalex' }, { label: 'arXiv', value: 'arxiv' }, { label: 'Semantic Scholar', value: 'semantic_scholar' }]} onChange={sources => onChange({ ...value, sources: sources as SearchSettings['sources'] })} />
    <p><Checkbox checked={value.crossrefFallback} disabled={disabled} onChange={event => onChange({ ...value, crossrefFallback: event.target.checked })}>结果较少时使用 Crossref 补充</Checkbox></p>
    <p><Checkbox checked={value.citationDepth === 1} disabled={disabled} onChange={event => onChange({ ...value, citationDepth: event.target.checked ? 1 : 0 })}>追踪一层参考文献与被引论文</Checkbox></p>
    {numericFields.map(field => <div key={field.key}><label className="simple-label" htmlFor={`search-${field.key}`}>{field.label}</label><InputNumber id={`search-${field.key}`} style={{ width: '100%' }} min={field.min} max={field.max} precision={0} value={value[field.key] as number} disabled={disabled} onChange={number => { if (number !== null) onChange({ ...value, [field.key]: number }); }} /></div>)}
    <p className="quiet-text">这些是预算上限，接口限流、可用记录和去重会影响实际数量。每个来源独立限速；使用 24 小时缓存。遇到部分来源失败会保留已取得结果和原因。</p>
    {(['openalex', 'semantic_scholar'] as const).map(source => <div key={source}>
      <label className="simple-label" htmlFor={`search-key-${source}`}>{source === 'openalex' ? 'OpenAlex' : 'Semantic Scholar'} API Key（可选）</label>
      <Input.Password id={`search-key-${source}`} autoComplete="new-password" value={keys[source] || ''} disabled={disabled} onChange={event => onKeyChange(source, event.target.value)} placeholder={keys[source] === null ? '保存后停用该源密钥' : settings?.searchKeys?.[source]?.hasKey ? '留空保留已配置密钥' : '可尝试匿名访问，接口可能限流'} />
      <Space><Button type="link" size="small" disabled={disabled} onClick={() => onKeyChange(source, null)}>清除密钥并停用环境变量回退</Button></Space>
    </div>)}
  </section>;
}
