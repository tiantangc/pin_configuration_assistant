async function run(wf, args) { return { wfKeys: Object.keys(wf||{}), wfType: typeof wf, argsType: typeof args, args }; }
