// The exact browser algorithms also execute here, without a browser or a model.
const R = require('./static/rules.js');
const AI = require('./static/ai.js');
const G = require('./static/game.js');
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', async () => {
  try {
    const q = JSON.parse(input);
    if (q.op === 'new') {
      process.stdout.write(JSON.stringify({state: R.newGame(q.seed, q.breaker || 0, q.mode || 'casual')}));
      return;
    }
    let action = q.action;
    if (q.op === 'plan') action = await AI.planTurn(q.state, {difficulty: q.difficulty, accuracy: q.accuracy, seed: q.seed});
    if (q.op === 'plan') { process.stdout.write(JSON.stringify({action})); return; }
    const c = G.createController({initialState: q.state, autoPlay: false, presentationDelay: 0});
    if (action.type === 'confirmPlacement' && action.position) {
      const placed = c.submit({type:'place', ...action.position}, q.actor);
      if (!placed.ok) throw Error(placed.reason);
    }
    const done = c.submit(action, q.actor);
    if (!done.ok) throw Error(done.reason);
    if (q.automatic && action.type === 'place' && c.getState().phase === 'placement') {
      const confirmed = c.submit({type:'confirmPlacement'}, q.actor);
      if (!confirmed.ok) throw Error(confirmed.reason);
    }
    let ticks = 0;
    while (c.getState().phase === 'motion' && ticks++ < 4000) c.tick(50);
    const state = c.getState();
    if (state.phase === 'error' || state.phase === 'motion') throw Error(state.error || '这杆未能停止');
    for (const key of ['planning','aiCue','aiLastAngle','submittedShotCount','presentation','pocketDrops']) delete state[key];
    process.stdout.write(JSON.stringify({state, motion_ms: ticks * 50}));
  } catch (e) {
    process.stdout.write(JSON.stringify({error: e.message}));
    process.exitCode = 1;
  }
});
