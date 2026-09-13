export default function PreflightList({ lines }) {
  return (
    <ul className="preflight-list">
      {lines.map((line, i) => (
        <li key={i} className={`preflight-list__item preflight-list__item--${line.level}`}>
          <span>{line.text}</span>
          {line.hint && <span className="preflight-list__hint">{line.hint}</span>}
        </li>
      ))}
    </ul>
  );
}
