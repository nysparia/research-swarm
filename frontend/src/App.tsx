import { Component, type ReactNode } from 'react';
import { Workspace } from './workbench/Workspace';
class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <div className="sw-fatal"><h2>界面暂时遇到问题</h2><p>任务与研究结果仍保存在本地服务中。</p><button className="sw-button sw-button-primary" onClick={() => window.location.reload()}>重新打开工作区</button></div> : this.props.children; }
}
export default function App() { return <Boundary><Workspace /></Boundary>; }
