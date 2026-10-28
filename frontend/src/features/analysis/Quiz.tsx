import { useEffect, useRef, useState } from 'react';
import { request } from '../../lib/api';
import type { Quiz, QuizState } from '../../lib/api';
import { formatClock } from '../../lib/format';
import './quiz.css';

const LETTERS = 'ABCDEF';
const SAVE_DELAY = 400;

type Props = {
  quiz: Quiz;
  onSeek?: (seconds: number) => void;
  /** Personal learning sidecar coordinates; answers are kept per analysis record. */
  projectId?: string;
  resource?: string;
  recordId?: string;
};

export default function QuizView({ quiz, onSeek, projectId, resource, recordId }: Props) {
  const [answers, setAnswers] = useState<Record<number, number>>({});
  const [notice, setNotice] = useState('');
  const timer = useRef<number | null>(null);
  const path = projectId && resource && recordId ? `/projects/${projectId}/study/quiz/${recordId}` : '';
  const query = resource ? `?resource=${encodeURIComponent(resource)}` : '';

  const answered = Object.keys(answers).length;
  const correct = quiz.questions.reduce((sum, item, index) => answers[index] === item.answer ? sum + 1 : sum, 0);

  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    request<QuizState>(path + query, controller.signal).then(state => {
      if (controller.signal.aborted) return;
      const stored = Object.fromEntries(Object.entries(state.answers).map(([key, value]) => [Number(key), value]));
      // A click made before the response arrived wins over the stored answer.
      setAnswers(current => ({ ...stored, ...current }));
    }).catch(() => {
      if (!controller.signal.aborted) setNotice('答题记录读取失败，本次作答仍可继续，但刷新后可能丢失。');
    });
    return () => controller.abort();
  }, [path, query]);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  function persist(next: Record<number, number>) {
    if (!path) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      void request<QuizState>(path, new AbortController().signal, {
        resource: resource || '', answers: Object.fromEntries(Object.entries(next).map(([key, value]) => [String(key), value])),
      }).then(() => setNotice('')).catch(() => setNotice('答题记录保存失败，刷新后可能丢失。'));
    }, SAVE_DELAY);
  }

  function choose(index: number, option: number) {
    const next = { ...answers, [index]: option };
    setAnswers(next);
    persist(next);
  }

  function reset() {
    setAnswers({});
    persist({});
  }

  return <section className="quiz" aria-label="复习自测题">
    <div className="quiz-heading">
      <div>
        <strong>{quiz.title || '复习自测题'}</strong>
        <span className="quiz-score">{`答对 ${correct} / ${answered} 题 · 共 ${quiz.questions.length} 题`}</span>
      </div>
      {answered > 0 && <button type="button" onClick={reset}>重新作答</button>}
    </div>
    {notice && <p className="quiz-notice" role="status">{notice}</p>}
    <ol className="quiz-list">
      {quiz.questions.map((item, index) => {
        const chosen = answers[index];
        const done = chosen !== undefined;
        const time = item.time;
        return <li key={index} className={done ? (chosen === item.answer ? 'is-correct' : 'is-wrong') : ''}>
          <div className="quiz-question">
            <span className="quiz-index" aria-hidden="true">{index + 1}</span>
            <p>{item.question}</p>
            {time !== null && <button type="button" className="quiz-time" disabled={!onSeek}
              aria-label={`跳到第 ${index + 1} 题的时间点 ${formatClock(time)}`} onClick={() => onSeek?.(time)}>{formatClock(time)}</button>}
          </div>
          <div className="quiz-options" role="group" aria-label={`第 ${index + 1} 题选项`}>
            {item.options.map((option, optionIndex) => {
              const state = !done ? '' : optionIndex === item.answer ? ' correct' : optionIndex === chosen ? ' wrong' : '';
              return <button type="button" key={optionIndex} className={'quiz-option' + state} aria-pressed={chosen === optionIndex}
                onClick={() => choose(index, optionIndex)}>
                <span>{LETTERS[optionIndex]}</span>{option}
              </button>;
            })}
          </div>
          {done && <div className="quiz-answer" role="status">
            <p><strong>{chosen === item.answer ? '回答正确' : '回答错误'}</strong>{` · 正确答案 ${LETTERS[item.answer]}. ${item.answer_text}`}</p>
            {item.explanation && <p><span className="quiz-answer-label">解析</span>{item.explanation}</p>}
          </div>}
        </li>;
      })}
    </ol>
  </section>;
}
