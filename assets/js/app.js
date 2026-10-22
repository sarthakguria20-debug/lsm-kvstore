/**
 * LSM-Tree Key-Value Engine UI Controller & Visualizer
 */

document.addEventListener("DOMContentLoaded", () => {
    const lsm = new SimLSMTree(4);

    // DOM Elements
    const memtableCapacityBar = document.getElementById("memtable-capacity-bar");
    const memtableCountText = document.getElementById("memtable-count");
    const memtableList = document.getElementById("memtable-nodes");
    const walFeed = document.getElementById("wal-feed");
    const sstablesContainer = document.getElementById("sstables-container");
    const queryTraceContainer = document.getElementById("query-trace-steps");
    const queryTraceResult = document.getElementById("query-trace-result");

    // Stats HUD Elements
    const statPuts = document.getElementById("stat-puts");
    const statGets = document.getElementById("stat-gets");
    const statBloomSkips = document.getElementById("stat-bloom-skips");
    const statFsyncs = document.getElementById("stat-fsyncs");
    const statSstables = document.getElementById("stat-sstables");
    const engineStatusBadge = document.getElementById("engine-status-badge");

    // Bloom Playground Elements
    const bloomInput = document.getElementById("bloom-input");
    const bloomAddBtn = document.getElementById("bloom-add-btn");
    const bloomTestBtn = document.getElementById("bloom-test-btn");
    const bloomBitsGrid = document.getElementById("bloom-bits-grid");
    const bloomHashesOutput = document.getElementById("bloom-hashes-output");
    const playgroundBloom = new SimBloomFilter(64, 3);

    // Interactive Terminal Elements
    const terminalOutput = document.getElementById("terminal-output");
    const terminalInput = document.getElementById("terminal-input");
    const commandHistory = [];
    let historyIdx = -1;

    // Toast notification
    function showToast(message, type = "info") {
        const toast = document.createElement("div");
        toast.className = `toast toast-${type}`;
        toast.innerHTML = `<span class="toast-dot"></span><span>${message}</span>`;
        document.body.appendChild(toast);
        setTimeout(() => toast.classList.add("visible"), 10);
        setTimeout(() => {
            toast.classList.remove("visible");
            setTimeout(() => toast.remove(), 300);
        }, 3200);
    }

    // Update Global Stats HUD
    function updateStatsHUD() {
        if (statPuts) statPuts.textContent = lsm.ioStats.totalPuts;
        if (statGets) statGets.textContent = lsm.ioStats.totalGets;
        if (statBloomSkips) statBloomSkips.textContent = lsm.ioStats.bloomFilteredSkips;
        if (statFsyncs) statFsyncs.textContent = lsm.ioStats.fsyncOps;
        if (statSstables) statSstables.textContent = lsm.sstables.length;

        if (engineStatusBadge) {
            if (lsm.systemCrashed) {
                engineStatusBadge.textContent = "CRASHED (TORN WRITE)";
                engineStatusBadge.className = "status-badge status-crashed";
            } else {
                engineStatusBadge.textContent = "HEALTHY & DURABLE";
                engineStatusBadge.className = "status-badge status-healthy";
            }
        }
    }

    // Render MemTable (SkipList)
    function renderMemTable() {
        const nodes = lsm.memTable.getTowerStructure();
        const count = nodes.length;
        const max = lsm.maxMemTableKeys;

        if (memtableCountText) {
            memtableCountText.textContent = `${count} / ${max} keys`;
        }
        if (memtableCapacityBar) {
            const percent = Math.min(100, Math.round((count / max) * 100));
            memtableCapacityBar.style.width = `${percent}%`;
            if (percent >= 100) {
                memtableCapacityBar.classList.add("full");
            } else {
                memtableCapacityBar.classList.remove("full");
            }
        }

        if (!memtableList) return;
        memtableList.innerHTML = "";

        if (nodes.length === 0) {
            memtableList.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">📂</div>
                    <div class="empty-title">MemTable is Empty</div>
                    <div class="empty-desc">New writes will append to the WAL and buffer into this SkipList.</div>
                </div>
            `;
            return;
        }

        nodes.forEach(node => {
            const card = document.createElement("div");
            card.className = `skiplist-tower ${node.isTombstone ? "tombstone" : ""}`;

            // Build vertical levels for SkipList visualization
            let levelsHtml = `<div class="tower-levels">`;
            for (let lvl = lsm.memTable.maxLevel; lvl >= 1; lvl--) {
                const active = lvl <= node.height;
                levelsHtml += `
                    <div class="level-indicator ${active ? 'active-level' : 'inactive-level'}" title="Level ${lvl}">
                        <span>L${lvl}</span>
                    </div>
                `;
            }
            levelsHtml += `</div>`;

            card.innerHTML = `
                ${levelsHtml}
                <div class="tower-content">
                    <div class="tower-header">
                        <span class="key-badge">${escapeHtml(node.key)}</span>
                        ${node.isTombstone ? '<span class="tombstone-tag">🪦 TOMBSTONE</span>' : `<span class="lsn-tag">LSN #${node.lsn}</span>`}
                    </div>
                    <div class="tower-val">
                        ${node.isTombstone ? '<span class="deleted-text">DELETED</span>' : `<code>${escapeHtml(node.value || "")}</code>`}
                    </div>
                </div>
            `;
            memtableList.appendChild(card);
        });
    }

    // Render WAL Stream
    function renderWAL() {
        if (!walFeed) return;
        walFeed.innerHTML = "";

        if (lsm.wal.length === 0) {
            walFeed.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">📝</div>
                    <div class="empty-title">WAL is Clean</div>
                    <div class="empty-desc">All writes have been flushed or database is uninitialized.</div>
                </div>
            `;
            return;
        }

        // Render from newest to oldest
        for (let i = lsm.wal.length - 1; i >= 0; i--) {
            const entry = lsm.wal[i];
            const item = document.createElement("div");
            item.className = `wal-item ${entry.corrupted ? "wal-corrupted" : ""}`;

            item.innerHTML = `
                <div class="wal-header">
                    <div class="wal-title-group">
                        <span class="wal-op ${entry.type.toLowerCase().includes('del') ? 'wal-del' : 'wal-put'}">${entry.type}</span>
                        <span class="wal-key">${escapeHtml(entry.key)}</span>
                    </div>
                    <div class="wal-badges">
                        <span class="wal-lsn">LSN #${entry.lsn}</span>
                        ${entry.corrupted ? 
                            '<span class="wal-fsync-badge fsync-error"><span class="pulse-red"></span> TORN FRAME</span>' : 
                            '<span class="wal-fsync-badge"><span class="pulse-green"></span> HARDWARE FSYNCED</span>'
                        }
                    </div>
                </div>
                <div class="wal-body">
                    <div class="wal-binary-details">
                        <span class="wal-crc" title="CRC32 Checksum">CRC32: <code>${entry.crc32}</code></span>
                        <span class="wal-size" title="Framed Byte Size">Size: <code>${entry.length}B</code></span>
                        <span class="wal-time">${entry.timestamp}</span>
                    </div>
                    ${entry.value !== null ? `<div class="wal-val-preview">Val: <code>${escapeHtml(entry.value)}</code></div>` : ''}
                </div>
            `;
            walFeed.appendChild(item);
        }
    }

    // Render SSTables
    function renderSSTables() {
        if (!sstablesContainer) return;
        sstablesContainer.innerHTML = "";

        if (lsm.sstables.length === 0) {
            sstablesContainer.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">💾</div>
                    <div class="empty-title">No SSTables on Disk</div>
                    <div class="empty-desc">When MemTable hits 4 keys or manual flush is clicked, an immutable SSTable is written with Bloom Filter + Sparse Index.</div>
                </div>
            `;
            return;
        }

        // Render newest first
        for (let i = lsm.sstables.length - 1; i >= 0; i--) {
            const sst = lsm.sstables[i];
            const card = document.createElement("div");
            card.className = "sstable-card";

            // Generate Bloom Filter bit dots (64 bits)
            let bloomBitsHtml = `<div class="sstable-bloom-grid">`;
            for (let b = 0; b < sst.bloomFilter.numBits; b++) {
                const isSet = sst.bloomFilter.bits[b] === 1;
                bloomBitsHtml += `<span class="mini-bit ${isSet ? 'set' : 'unset'}" title="Bit ${b}: ${isSet ? '1' : '0'}"></span>`;
            }
            bloomBitsHtml += `</div>`;

            // Sparse Index entries
            let indexRowsHtml = "";
            sst.sparseIndex.forEach((entry, idx) => {
                indexRowsHtml += `
                    <tr>
                        <td><code>${escapeHtml(entry.key)}</code></td>
                        <td>0x${entry.offset.toString(16).padStart(4, "0")}</td>
                        <td>Block #${idx}</td>
                    </tr>
                `;
            });

            // Data Blocks entries
            let dataBlocksHtml = "";
            sst.dataBlocks.forEach((block, bIdx) => {
                let blockRecordsHtml = "";
                block.records.forEach(r => {
                    blockRecordsHtml += `
                        <div class="sstable-record ${r.isTombstone ? 'tombstone' : ''}">
                            <span class="record-key">${escapeHtml(r.key)}</span>
                            ${r.isTombstone ? '<span class="tomb-icon">🪦 DELETED</span>' : `<span class="record-val">${escapeHtml(r.value || "")}</span>`}
                        </div>
                    `;
                });

                dataBlocksHtml += `
                    <div class="data-block-box">
                        <div class="data-block-header">
                            <span>Block #${bIdx}</span>
                            <span class="block-offset">Offset 0x${block.offset.toString(16)}</span>
                        </div>
                        <div class="data-block-content">
                            ${blockRecordsHtml}
                        </div>
                    </div>
                `;
            });

            card.innerHTML = `
                <div class="sstable-header">
                    <div class="sstable-title">
                        <span class="sst-icon">🗄️</span>
                        <span class="sst-name">${sst.id}</span>
                    </div>
                    <div class="sstable-meta">
                        <span class="badge badge-subtle">${sst.recordCount} records</span>
                        <span class="badge badge-subtle">${sst.totalSizeBytes} bytes</span>
                        <span class="badge badge-subtle">${sst.createdAt}</span>
                    </div>
                </div>
                <div class="sstable-sections">
                    <div class="sstable-section">
                        <div class="section-label">
                            <span>1. Bloom Filter (RAM)</span>
                            <span class="section-sub">64-bit Bitset &bull; 3 Hashes</span>
                        </div>
                        ${bloomBitsHtml}
                    </div>

                    <div class="sstable-section">
                        <div class="section-label">
                            <span>2. Sparse Index (RAM)</span>
                            <span class="section-sub">Binary Search Lookup</span>
                        </div>
                        <table class="sparse-index-table">
                            <thead>
                                <tr><th>Key</th><th>Offset</th><th>Target</th></tr>
                            </thead>
                            <tbody>
                                ${indexRowsHtml}
                            </tbody>
                        </table>
                    </div>

                    <div class="sstable-section">
                        <div class="section-label">
                            <span>3. Clustered Data Blocks (Disk)</span>
                            <span class="section-sub">Atomic POSIX Flush</span>
                        </div>
                        <div class="data-blocks-list">
                            ${dataBlocksHtml}
                        </div>
                    </div>
                </div>
            `;
            sstablesContainer.appendChild(card);
        }
    }

    // Refresh entire UI
    function refreshUI() {
        renderMemTable();
        renderWAL();
        renderSSTables();
        updateStatsHUD();
    }

    // Operations Form Handlers
    const putForm = document.getElementById("put-form");
    if (putForm) {
        putForm.addEventListener("submit", (e) => {
            e.preventDefault();
            const keyInput = document.getElementById("put-key");
            const valInput = document.getElementById("put-val");
            const key = keyInput.value.trim();
            const val = valInput.value.trim();

            if (!key) return;

            try {
                const res = lsm.put(key, val);
                refreshUI();
                showToast(`PUT '${key}' committed! WAL LSN #${res.lsn} fsynced to disk.`, "success");
                if (res.flushed) {
                    showToast(`MemTable capacity reached. Flushed to SSTable!`, "info");
                }
                keyInput.value = "";
                valInput.value = "";
                keyInput.focus();
            } catch (err) {
                showToast(err.message, "error");
            }
        });
    }

    const delForm = document.getElementById("del-form");
    if (delForm) {
        delForm.addEventListener("submit", (e) => {
            e.preventDefault();
            const keyInput = document.getElementById("del-key");
            const key = keyInput.value.trim();
            if (!key) return;

            try {
                const res = lsm.delete(key);
                refreshUI();
                showToast(`DELETE tombstone 🪦 inserted for '${key}' (LSN #${res.lsn}).`, "warning");
                if (res.flushed) {
                    showToast(`MemTable capacity reached. Flushed to SSTable!`, "info");
                }
                keyInput.value = "";
            } catch (err) {
                showToast(err.message, "error");
            }
        });
    }

    // Query Tracer (GET Form)
    const getForm = document.getElementById("get-form");
    if (getForm) {
        getForm.addEventListener("submit", (e) => {
            e.preventDefault();
            const keyInput = document.getElementById("get-key");
            const key = keyInput.value.trim();
            if (!key) return;

            try {
                const trace = lsm.get(key);
                renderQueryTrace(trace);
                updateStatsHUD();
            } catch (err) {
                showToast(err.message, "error");
            }
        });
    }

    // Render Query Trace Steps
    function renderQueryTrace(trace) {
        if (!queryTraceContainer || !queryTraceResult) return;
        queryTraceContainer.innerHTML = "";

        trace.steps.forEach((step, idx) => {
            const stepEl = document.createElement("div");
            stepEl.className = "trace-step";
            stepEl.style.animationDelay = `${idx * 80}ms`;

            const isSkip = step.stage.includes("Bloom Filter Skip");
            const isHit = step.stage.includes("Hit");

            stepEl.innerHTML = `
                <div class="trace-step-num">${idx + 1}</div>
                <div class="trace-step-content">
                    <div class="trace-step-stage ${isSkip ? 'text-skip' : ''} ${isHit ? 'text-hit' : ''}">
                        ${escapeHtml(step.stage)}
                    </div>
                    <div class="trace-step-desc">${escapeHtml(step.desc)}</div>
                </div>
            `;
            queryTraceContainer.appendChild(stepEl);
        });

        let resultHtml = "";
        if (trace.found) {
            if (trace.isTombstone) {
                resultHtml = `
                    <div class="trace-box trace-tombstone">
                        <div class="trace-box-title">🪦 KEY DELETED (Tombstone Found)</div>
                        <div class="trace-box-desc">Key '<code>${escapeHtml(trace.key)}</code>' was found in <strong>${trace.location}</strong> with a tombstone record.</div>
                    </div>
                `;
            } else {
                resultHtml = `
                    <div class="trace-box trace-success">
                        <div class="trace-box-title">✅ KEY FOUND in ${trace.location}</div>
                        <div class="trace-box-value">Value: <code>${escapeHtml(trace.value)}</code></div>
                    </div>
                `;
            }
        } else {
            resultHtml = `
                <div class="trace-box trace-miss">
                    <div class="trace-box-title">❌ KEY NOT FOUND</div>
                    <div class="trace-box-desc">Key '<code>${escapeHtml(trace.key)}</code>' is not present in the active MemTable or any immutable SSTable.</div>
                </div>
            `;
        }
        queryTraceResult.innerHTML = resultHtml;
    }

    // Action Buttons
    document.getElementById("btn-seed-data")?.addEventListener("click", () => {
        try {
            lsm.put("user:101", '{"name":"Alice","role":"admin"}');
            lsm.put("user:102", '{"name":"Bob","role":"engineer"}');
            lsm.put("user:103", '{"name":"Charlie","role":"product"}');
            lsm.put("order:9001", '{"item":"Server Unit","amount":1200}');
            refreshUI();
            showToast("Seeded 4 demo records. Triggered automatic SSTable flush!", "success");
        } catch (e) {
            showToast(e.message, "error");
        }
    });

    document.getElementById("btn-flush")?.addEventListener("click", () => {
        try {
            const sst = lsm.flushMemTable();
            if (sst) {
                refreshUI();
                showToast(`MemTable flushed to ${sst.id} with Bloom Filter and Sparse Index!`, "success");
            } else {
                showToast("MemTable is empty; nothing to flush.", "warning");
            }
        } catch (e) {
            showToast(e.message, "error");
        }
    });

    document.getElementById("btn-compact")?.addEventListener("click", () => {
        try {
            const res = lsm.compact();
            if (res) {
                refreshUI();
                showToast(`Compaction finished! Merged ${res.oldTableCount} SSTables into ${res.newTable.id}. Purged ${res.purgedRecords} dead/superseded records.`, "success");
            } else {
                showToast("At least 2 SSTables are required to perform K-way compaction.", "warning");
            }
        } catch (e) {
            showToast(e.message, "error");
        }
    });

    document.getElementById("btn-crash")?.addEventListener("click", () => {
        if (lsm.systemCrashed) {
            showToast("System is already in a crashed state. Run recovery!", "error");
            return;
        }
        const fakeLsn = lsm.simulateTornWriteCrash();
        refreshUI();
        showToast(`💥 POWER OUTAGE SIMULATED mid-write at LSN #${fakeLsn}! Corrupted torn frame written to disk. MemTable RAM wiped!`, "error");
    });

    document.getElementById("btn-recover")?.addEventListener("click", () => {
        if (!lsm.systemCrashed) {
            showToast("Database is healthy. No recovery needed.", "info");
            return;
        }
        const res = lsm.recover();
        refreshUI();
        showToast(`🛡️ ZERO DATA LOSS RECOVERY: Replayed ${res.recovered} valid CRC32 frames. Truncated ${res.truncatedTornFrames} torn frames. Database 100% consistent!`, "success");
    });

    // Bloom Filter Interactive Playground
    function renderPlaygroundBits() {
        if (!bloomBitsGrid) return;
        bloomBitsGrid.innerHTML = "";
        for (let i = 0; i < playgroundBloom.numBits; i++) {
            const isSet = playgroundBloom.bits[i] === 1;
            const bitEl = document.createElement("div");
            bitEl.className = `bloom-bit ${isSet ? 'set' : 'unset'}`;
            bitEl.id = `pg-bit-${i}`;
            bitEl.innerHTML = `<span class="bit-idx">${i}</span><span class="bit-val">${isSet ? '1' : '0'}</span>`;
            bloomBitsGrid.appendChild(bitEl);
        }
    }

    renderPlaygroundBits();

    bloomAddBtn?.addEventListener("click", () => {
        const val = bloomInput.value.trim();
        if (!val) return;
        const [h1, h2] = hashPair(val);
        const indices = playgroundBloom.add(val);
        renderPlaygroundBits();

        // Flash added bits
        indices.forEach(idx => {
            const el = document.getElementById(`pg-bit-${idx}`);
            if (el) el.classList.add("pulse-highlight");
        });

        if (bloomHashesOutput) {
            bloomHashesOutput.innerHTML = `
                <div class="hash-result">
                    Added: '<strong>${escapeHtml(val)}</strong>'<br/>
                    Kirsch-Mitzenmacher: h1 = <code>0x${h1.toString(16)}</code>, h2 = <code>0x${h2.toString(16)}</code><br/>
                    Flipped Bit Positions: <code>[${indices.join(", ")}]</code>
                </div>
            `;
        }
        showToast(`Key '${val}' added to Bloom filter!`, "success");
    });

    bloomTestBtn?.addEventListener("click", () => {
        const val = bloomInput.value.trim();
        if (!val) return;
        const [h1, h2] = hashPair(val);
        const res = playgroundBloom.check(val);

        if (bloomHashesOutput) {
            if (res.hit) {
                bloomHashesOutput.innerHTML = `
                    <div class="hash-result hash-hit">
                        Checking: '<strong>${escapeHtml(val)}</strong>'<br/>
                        Tested Bits: <code>[${res.bitIndices.join(", ")}]</code> &rarr; <strong>ALL BITS ARE 1</strong> (MAYBE PRESENT)
                    </div>
                `;
                showToast(`Bloom filter HIT for '${val}' (May be present)`, "info");
            } else {
                bloomHashesOutput.innerHTML = `
                    <div class="hash-result hash-miss">
                        Checking: '<strong>${escapeHtml(val)}</strong>'<br/>
                        Tested Bits: <code>[${res.bitIndices.join(", ")}]</code> &rarr; <strong>Bit #${res.failedIndex} is 0!</strong><br/>
                        ⚡ <strong>DEFINITELY NOT PRESENT (Instant reject &mdash; Zero disk I/O!)</strong>
                    </div>
                `;
                showToast(`Bloom filter REJECTED '${val}'! Zero disk I/O!`, "success");
            }
        }
    });

    // Terminal Emulator
    function printTerm(text, className = "term-line") {
        if (!terminalOutput) return;
        const line = document.createElement("div");
        line.className = className;
        line.innerHTML = text;
        terminalOutput.appendChild(line);
        terminalOutput.scrollTop = terminalOutput.scrollHeight;
    }

    function handleCommand(cmdStr) {
        const trimmed = cmdStr.trim();
        if (!trimmed) return;
        printTerm(`<span class="term-prompt">lsm-engine&gt;</span> ${escapeHtml(trimmed)}`);
        commandHistory.push(trimmed);
        historyIdx = commandHistory.length;

        const parts = trimmed.split(/\s+/);
        const cmd = parts[0].toLowerCase();

        switch (cmd) {
            case "help":
                printTerm(`Available Commands:
  put &lt;key&gt; &lt;value&gt;    - Write key-value pair (atomic WAL fsync + MemTable)
  get &lt;key&gt;            - Query key through Bloom Filter and SSTables
  del &lt;key&gt;            - Write tombstone for key deletion
  scan &lt;start&gt; &lt;end&gt;   - Range scan sorted keys
  flush                 - Force flush MemTable to SSTable
  compact               - K-way merge compaction across SSTables
  crash                 - Simulate power loss during write (torn frame)
  recover               - Run recovery and replay valid WAL frames
  stats                 - Show engine runtime I/O statistics
  clear                 - Clear terminal screen
  help                  - Show this help menu`);
                break;

            case "put":
                if (parts.length < 3) {
                    printTerm("Error: Usage: put &lt;key&gt; &lt;value&gt;", "term-error");
                    return;
                }
                const key = parts[1];
                const val = parts.slice(2).join(" ");
                try {
                    const r = lsm.put(key, val);
                    refreshUI();
                    printTerm(`OK (LSN: ${r.lsn}, WAL CRC32: ${r.walEntry.crc32}, Fsync: Hardware OK)`);
                    if (r.flushed) printTerm("--> MemTable full: Flushed to SSTable");
                } catch (e) {
                    printTerm(`Error: ${e.message}`, "term-error");
                }
                break;

            case "get":
                if (parts.length < 2) {
                    printTerm("Error: Usage: get &lt;key&gt;", "term-error");
                    return;
                }
                try {
                    const t = lsm.get(parts[1]);
                    refreshUI();
                    if (t.found) {
                        if (t.isTombstone) {
                            printTerm(`(nil) [Tombstone found in ${t.location}]`);
                        } else {
                            printTerm(`"${escapeHtml(t.value)}" [Found in ${t.location}]`);
                        }
                    } else {
                        printTerm("(nil) [Key not found]");
                    }
                } catch (e) {
                    printTerm(`Error: ${e.message}`, "term-error");
                }
                break;

            case "del":
                if (parts.length < 2) {
                    printTerm("Error: Usage: del &lt;key&gt;", "term-error");
                    return;
                }
                try {
                    const d = lsm.delete(parts[1]);
                    refreshUI();
                    printTerm(`OK (Tombstone LSN: ${d.lsn})`);
                } catch (e) {
                    printTerm(`Error: ${e.message}`, "term-error");
                }
                break;

            case "scan":
                const sKey = parts[1] || "";
                const eKey = parts[2] || "";
                try {
                    const res = lsm.scan(sKey, eKey);
                    if (res.length === 0) {
                        printTerm("(empty range)");
                    } else {
                        res.forEach(item => {
                            printTerm(`  ${item.key} &rarr; "${item.value}" (LSN #${item.lsn})`);
                        });
                        printTerm(`Total: ${res.length} keys`);
                    }
                } catch (e) {
                    printTerm(`Error: ${e.message}`, "term-error");
                }
                break;

            case "flush":
                try {
                    const s = lsm.flushMemTable();
                    refreshUI();
                    if (s) {
                        printTerm(`OK: MemTable flushed into ${s.id} (${s.recordCount} records, ${s.totalSizeBytes} bytes).`);
                    } else {
                        printTerm("MemTable is empty.");
                    }
                } catch (e) {
                    printTerm(`Error: ${e.message}`, "term-error");
                }
                break;

            case "compact":
                try {
                    const c = lsm.compact();
                    refreshUI();
                    if (c) {
                        printTerm(`OK: Compacted ${c.oldTableCount} SSTables into ${c.newTable.id}. Purged ${c.purgedRecords} dead records.`);
                    } else {
                        printTerm("Error: At least 2 SSTables required for compaction.", "term-error");
                    }
                } catch (e) {
                    printTerm(`Error: ${e.message}`, "term-error");
                }
                break;

            case "crash":
                lsm.simulateTornWriteCrash();
                refreshUI();
                printTerm(`💥 CRASH TRIGGERED: Power outage cut during write. Torn frame appended to WAL. Volatile RAM MemTable erased! Type 'recover' to restore.`, "term-error");
                break;

            case "recover":
                const rec = lsm.recover();
                refreshUI();
                printTerm(`🛡️ RECOVERY COMPLETE: Verified CRC32 on all frames. Replayed ${rec.recovered} operations. Cleanly truncated ${rec.truncatedTornFrames} torn frames. Database consistent!`, "term-success");
                break;

            case "stats":
                printTerm(`Engine Statistics:
  Total Puts:                ${lsm.ioStats.totalPuts}
  Total Gets:                ${lsm.ioStats.totalGets}
  Bloom Filter 0-IO Skips:   ${lsm.ioStats.bloomFilteredSkips}
  Hardware Fsyncs:           ${lsm.ioStats.fsyncOps}
  Disk Block Reads:          ${lsm.ioStats.diskBlockReads}
  Active SSTables:           ${lsm.sstables.length}
  MemTable Keys:             ${lsm.memTable.count}`);
                break;

            case "clear":
                if (terminalOutput) terminalOutput.innerHTML = "";
                break;

            default:
                printTerm(`Unknown command: '${escapeHtml(cmd)}'. Type 'help' for available commands.`, "term-error");
                break;
        }
    }

    if (terminalInput) {
        terminalInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                const cmd = terminalInput.value;
                terminalInput.value = "";
                handleCommand(cmd);
            } else if (e.key === "ArrowUp") {
                if (historyIdx > 0) {
                    historyIdx--;
                    terminalInput.value = commandHistory[historyIdx] || "";
                }
            } else if (e.key === "ArrowDown") {
                if (historyIdx < commandHistory.length - 1) {
                    historyIdx++;
                    terminalInput.value = commandHistory[historyIdx] || "";
                } else {
                    historyIdx = commandHistory.length;
                    terminalInput.value = "";
                }
            }
        });
    }

    function escapeHtml(str) {
        if (str === null || str === undefined) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    // Initialize with a couple demo records
    lsm.put("db:status", "online");
    lsm.put("user:admin", '{"id":1,"access":"root"}');
    refreshUI();
});
