import { useEffect, useState } from 'react';
import type { Quiz } from '../../lib/api';
import { formatClock } from '../../lib/format';
import './quiz.css';
import './exam.css';

const LETTERS = 'ABCDEF';
const LIMITS = [0, 5, 10, 15, 20, 30, 45, 60];

type Props = { quiz: Quiz; onSeek?: (seconds: number) => void };

/** Answer everything first, grade once: the review quiz shows the answer as soon as you pick. */
export default function Exam({ quiz, onSeek }: Props) {
  const [answers, setAnswers] = useState<Record<number, number>>({});
  const [submitted, setSubmitted] = useState(false);
  const [limit, setLimit] = useState(0);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [used, setUsed] = useState(0);
  const [timedOut, setTimedOut] = useState(false);

  const total = quiz.questions.length;
  const answered = Object.keys(answers).length;
  const correct = quiz.questions.reduce((sum, item, index) => answers[index] === item.answer ? sum + 1 : sum, 0);
  const remaining = limit > 0 && startedAt !== null ? Math.max(0, limit * 60 - elapsed) : null;
  const wrong = quiz.questions.flatMap((item, index) => answers[index] === item.answer ? [] : [index]);
  const percent = total ? Math.round(correct / total * 100) : 0;

  useEffect(() => {
    if (submitted || startedAt === null) return;
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - startedAt) / 1000)), 500);
    return () => clearInterval(timer);
  }, [submitted, startedAt]);

  function submit(auto = false) {
    if (submitted) return;
    setUsed(startedAt === null ? 0 : Math.floor((Date.now() - startedAt) / 1000));
    setTimedOut(auto);
    setSubmitted(true);
  }

  // Running out of time grades whatever is on screen; unanswered questions count as wrong.
  useEffect(() => { if (!submitted && remaining === 0) submit(true); }, [remaining, submitted]);

  function restart() {
    setAnswers({}); setSubmitted(false); setUsed(0); setTimedOut(false); setElapsed(0);
    setStartedAt(limit > 0 ? Date.now() : null);
  }

  function chooseLimit(minutes: number) {
    setLimit(minutes); setElapsed(0); setStartedAt(minutes > 0 ? Date.now() : null);
  }

  return <section className="quiz exam" aria-label="模拟测验">
    <div className="quiz-heading">
      <div>
        <strong>{quiz.title || '模拟测验'}</strong>
        <span className="quiz-score">{submitted
          ? <>{`得分 ${correct} / ${total} · 正确率 ${percent}%`}{startedAt !== null && ` · 用时 ${formatClock(used)}`}</>
          : <>{`已答 ${answered} / ${total} 题`}{remaining !== null && ` · 剩余 ${formatClock(remaining)}`}</>}</span>
      </div>
      <div className="exam-actions">
        {!submitted && <label className="exam-limit">
          <span>限时</span>
          <select aria-label="限时" value={limit} onChange={event => chooseLimit(Number(event.target.value))}>
            {LIMITS.map(value => <option key={value} value={value}>{value ? `${value} 分钟` : '不限时'}</option>)}
          </select>
        </label>}
        {!submitted && <button type="button" className="primary" disabled={!answered} onClick={() => submit(false)}>
          {answered < total ? `提交答案（${total - answered} 题未答）` : '提交答案'}</button>}
        {submitted && <button type="button" onClick={restart}>重新测验</button>}
      </div>
    </div>
    {!submitted && remaining !== null && remaining <= 60 && <p className="exam-warning" role="alert">剩余不足 1 分钟，时间到会自动提交。</p>}
    <ol className="quiz-list">
      {quiz.questions.map((item, index) => {
        const chosen = answers[index];
        const time = item.time;
        return <li key={index} className={submitted ? (chosen === item.answer ? 'is-correct' : 'is-wrong') : ''}>
          <div className="quiz-question">
            <span className="quiz-index" aria-hidden="true">{index + 1}</span>
            <p>{item.question}</p>
            {submitted && time !== null && <button type="button" className="quiz-time" disabled={!onSeek}
              aria-label={`跳到第 ${index + 1} 题的时间点 ${formatClock(time)}`} onClick={() => onSeek?.(time)}>{formatClock(time)}</button>}
          </div>
          <div className="quiz-options" role="group" aria-label={`第 ${index + 1} 题选项`}>
            {item.options.map((option, optionIndex) => {
              const state = !submitted ? '' : optionIndex === item.answer ? ' correct' : optionIndex === chosen ? ' wrong' : '';
              return <button type="button" key={optionIndex} className={'quiz-option' + state} aria-pressed={chosen === optionIndex}
                disabled={submitted} onClick={() => setAnswers(current => ({ ...current, [index]: optionIndex }))}>
                <span>{LETTERS[optionIndex]}</span>{option}
              </button>;
            })}
          </div>
          {submitted && <div className="quiz-answer" role="status">
            <p><strong>{chosen === item.answer ? '回答正确' : chosen === undefined ? '未作答' : '回答错误'}</strong>{` · 正确答案 ${LETTERS[item.answer]}. ${item.answer_text}`}</p>
            {item.explanation && <p><span className="quiz-answer-label">解析</span>{item.explanation}</p>}
          </div>}
        </li>;
      })}
    </ol>
    {submitted && <div className="exam-summary" role="status">
      <p><strong>{`得分 ${correct} / ${total}（${percent}%）`}</strong>{timedOut ? ' · 时间到，已自动提交' : ''}{answered < total ? ` · ${total - answered} 题未作答` : ''}</p>
      <p>{wrong.length ? `错题：${wrong.map(index => `第 ${index + 1} 题`).join('、')}` : '全部答对。'}</p>
    </div>}
  </section>;
}
