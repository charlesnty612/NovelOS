import { Link } from 'react-router-dom';

export function NotFoundPage() {
  return (
    <div style={{ textAlign: 'center', padding: 60 }}>
      <h2>页面不存在</h2>
      <p className="muted">你访问的路径未匹配任何路由。</p>
      <Link to="/" className="btn btn--primary" style={{ marginTop: 16, display: 'inline-block' }}>
        回到项目列表
      </Link>
    </div>
  );
}
