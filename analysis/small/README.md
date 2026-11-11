# Small Queue Modelling and Analysis

## 1. Real-World Scenario

For the small queueing system, I chose to model a computer lab where six computers are responsible for processing student-submitted jobs. Each computer has its own queue, and the jobs are assigned to available processing slots. If all processing slots are busy, additional jobs have to wait in the queue until one becomes available.

The purpose of this experiment is to see how the number of available processing slots affects job latency, throughput, queue utilization, and saturation. This is relevant to a computer lab because students may submit multiple jobs around the same time, especially during busy periods, and having too few processing resources could cause delays.

## 2. Mapping the Scenario to a Queueing System

The computer lab is represented using six independent queueing systems, one for each computer.

| Queueing Component | Real-World Equivalent |
| --- | --- |
| Six queues (q0–q5) | Six computers in the lab |
| Source workload | Jobs submitted by students |
| Queue depth of 100 | Maximum number of waiting jobs per computer |
| Four servers per queue | Four processing slots available on each computer |
| Server function | Processing a submitted job |
| Wait task of 0.050 seconds | Fixed amount of processing time for a job |
| Sink | Completed jobs leaving the computer |
| Queue saturation | Jobs waiting for processing |
| Queue utilization | How busy the processing slots are |
| Job latency | Time between submitting and completing a job |
| Dropped jobs | Jobs rejected because the system cannot accept them |

Each queue operates independently, so jobs assigned to one computer do not move to another computer. The baseline configuration has four servers per queue, giving the overall system 24 processing slots.

This is a simplified model because actual student jobs would have different processing requirements. However, using the same task duration makes it easier to isolate the effect of changing the number of available servers. I also choose to be lazy.

## 3. System Configuration and Workload Generation

The experiment uses six queueing systems, each configured with a maximum queue depth of 100.

Each queue receives 100 jobs, giving a total workload of 600 jobs. Every job contains a single wait task of 0.050 seconds.

The workload is generated automatically and distributed across the six independent queues. The same number of jobs and the same task duration are used for each sensitivity scenario so that changes in performance can be compared against changes in server capacity.

The baseline configuration is:

| Parameter | Value |
| --- | --- |
| Number of queues | 6 |
| Jobs per queue | 100 |
| Total jobs | 600 |
| Queue depth | 100 |
| Servers per queue | 4 |
| Total servers | 24 |
| Task duration | 0.050 seconds |

The source files provide jobs to each queue, and the sink files record the completed jobs. The generated event logs and queue metrics are used by the analysis tools to calculate performance measurements and create plots.

Using a fixed workload is useful here because the main variable being tested is the number of servers. If the workload also changed between scenarios, it would be harder to determine whether performance differences were caused by the workload or the available processing capacity.

## 4. Important Metrics

The main metrics for this experiment are utilization, saturation, errors, latency, and throughput.

**Utilization** describes how busy the available processing resources are. A utilization of 1.0 indicates that the measured resources are fully utilized during the corresponding measurement interval.

**Saturation** measures how many jobs are waiting for processing because the servers are occupied. Higher saturation indicates greater queueing pressure.

**Errors** represent jobs that cannot be processed successfully, such as jobs dropped because a queue is full.

**Latency** measures the time between a job entering the system and completing. This includes both the time spent waiting and the time spent being processed.

**Throughput** measures the number of jobs completed over a period of time. Higher throughput means the system can process more jobs within that period.

These metrics are important together because a system can successfully process every job while still experiencing significant delays. For example, a system with no dropped jobs but high latency may still provide a poor experience for students using the computer lab.

## 5. Baseline Results

The baseline experiment uses six queues with four servers per queue, giving 24 total servers.

| Metric | Baseline Result |
| --- | --- |
| Total jobs | 600 |
| Completed jobs | 600 |
| Dropped jobs | 0 |
| Mean latency | 638.8 ms |
| Peak utilization | 1.000 |
| Peak saturation | 90.7 |

All 600 jobs were completed successfully without any drops.

The peak utilization of 1.0 shows that the processing resources reached full utilization during the experiment. The saturation measurement also indicates that jobs accumulated while waiting for available processing slots.

Even though each task only requires 50 milliseconds of processing time, the average end-to-end latency was 638.8 milliseconds. This difference indicates that queueing delays and other system overhead contributed substantially to the overall response time.

For the computer lab scenario, this means the existing processing capacity was sufficient to finish all submitted jobs, but students still experienced delays while waiting for their jobs to complete.

## 6. Sensitivity Analysis

To evaluate how the system responds to changes in available resources, I ran seven scenarios.

The baseline uses 24 servers. Three scenarios reduce the available server capacity by 10%, 20%, and 30%, while three others increase server capacity by 25%, 50%, and 100%.

The number of queues, jobs, and task duration remains the same across the scenarios.

### Sensitivity Results

| Scenario | Server Factor | Total Servers | Servers per Queue | Delivered | Dropped | Mean Latency (ms) | Peak Utilization | Peak Saturation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Baseline | 1.00 | 24 | 4 | 600 | 0 | 638.8 | 1.000 | 90.7 |
| -10% Servers | 0.90 | 22 | 3–4 | 600 | 0 | 717.5 | 1.000 | 92.1 |
| -20% Servers | 0.80 | 19 | 3–4 | 600 | 0 | 823.8 | 1.000 | 91.9 |
| -30% Servers | 0.70 | 17 | 2–3 | 600 | 0 | 935.6 | 1.000 | 94.4 |
| +25% Servers | 1.25 | 30 | 5 | 600 | 0 | 508.7 | 1.000 | 89.0 |
| +50% Servers | 1.50 | 36 | 6 | 600 | 0 | 420.0 | 1.000 | 87.6 |
| +100% Servers | 2.00 | 48 | 8 | 600 | 0 | 315.9 | 1.000 | 83.1 |

### Decreasing Server Capacity

Reducing server capacity consistently increased average job latency.

- **-10% servers:** Latency increased from 638.8 ms to 717.5 ms, approximately 12.3%.
- **-20% servers:** Latency increased to 823.8 ms, approximately 29.0%.
- **-30% servers:** Latency increased to 935.6 ms, approximately 46.5%.

The largest reduction in server capacity produced the highest latency and peak saturation.

This behavior makes sense because reducing the number of available processing slots means fewer jobs can be processed simultaneously. As a result, more jobs have to wait in the queues before receiving service.

In the university computer lab scenario, removing processing resources would make students wait longer for their submitted jobs to finish.

However, all 600 jobs were still completed in every scenario, indicating that reducing server capacity did not cause job loss under the tested workload.

### Increasing Server Capacity

Increasing server capacity consistently decreased average job latency.

- **+25% servers:** Latency decreased to 508.7 ms, approximately 20.4% lower than baseline.
- **+50% servers:** Latency decreased to 420.0 ms, approximately 34.3% lower than baseline.
- **+100% servers:** Latency decreased to 315.9 ms, approximately 50.5% lower than baseline.

Increasing the number of servers also reduced peak saturation, from 90.7 at baseline to 83.1 with twice the server capacity.

This indicates that additional processing resources helped reduce the amount of time jobs spent waiting.

For a university computer lab, adding processing slots would improve responsiveness when many students submit jobs around the same time.

However, doubling the number of servers did not eliminate latency entirely. Even with more resources, jobs still require processing time, and other overhead may contribute to response times.

## 7. Interpreting Utilization, Saturation, Throughput, and Latency

The results show a relationship between available server capacity and job latency.

Every scenario reached a peak utilization of 1.0, meaning the measured processing resources became fully utilized at some point. This suggests the workload was capable of placing significant demand on the available servers.

Saturation generally increased when server capacity was reduced and decreased when additional servers were provided. The -20% scenario had slightly lower peak saturation than the -10% scenario, so the relationship was not perfectly monotonic. However, the overall pattern indicates that additional resources reduced queueing pressure.

Latency followed a clearer pattern. Every reduction in server capacity increased average latency, while every increase in server capacity decreased it.

All scenarios delivered the same 600 jobs with no drops. This confirms that every configuration completed the offered workload, but it does not mean all configurations had identical throughput. Throughput depends on how quickly those jobs were completed, which is illustrated by the throughput plots.

The results demonstrate why latency, utilization, saturation, and throughput should be examined together rather than relying on a single metric.

A system can have zero errors while still suffering from substantial queueing delays.

## 8. Accessory Analysis and Plotting Tools

The experiment also generates plots using the queue and event analysis tools described in the lab.

Six main plots are generated for each scenario:

| Plot | Purpose |
| --- | --- |
| `utilization.png` | Shows processing-resource utilization over time |
| `saturation.png` | Shows how many jobs are waiting in queues |
| `errors.png` | Shows job errors and drops over time |
| `latency.png` | Shows job response times |
| `throughput.png` | Shows job completion throughput |
| `longest-jobs.png` | Traces the three longest-running jobs |

Each scenario also includes an accessory `tools/` directory containing additional figures such as:

- `use.png` for utilization, saturation, and error analysis.
- `resplot.png` and response plots for system response-time analysis.
- `etrace.png` for tracing individual job events.

The event trace displays the first 40 jobs, while the longest-jobs plot focuses on the three jobs with the longest completion times.

These tools help identify potential performance problems beyond the overall averages. For example, event traces can show how individual jobs move through the system, while latency and saturation plots help identify periods when processing resources are under heavy demand.

The plots were generated automatically during the sensitivity runs.

## 9. Supporting Plots

The following folders contain the generated plots for each sensitivity scenario:

| Scenario | Folder |
| --- | --- |
| Baseline | `results/base/` |
| -10% Servers | `results/srv-10/` |
| -20% Servers | `results/srv-20/` |
| -30% Servers | `results/srv-30/` |
| +25% Servers | `results/srv+25/` |
| +50% Servers | `results/srv+50/` |
| +100% Servers | `results/srv+100/` |

### Baseline

![Baseline Utilization](results/base/utilization.png)

![Baseline Saturation](results/base/saturation.png)

![Baseline Latency](results/base/latency.png)

![Baseline Throughput](results/base/throughput.png)

![Baseline Errors](results/base/errors.png)

![Baseline Longest Jobs](results/base/longest-jobs.png)

### Reduced Server Capacity

#### -10% Servers

![Minus 10 Percent Latency](results/srv-10/latency.png)

![Minus 10 Percent Saturation](results/srv-10/saturation.png)

#### -20% Servers

![Minus 20 Percent Latency](results/srv-20/latency.png)

![Minus 20 Percent Saturation](results/srv-20/saturation.png)

#### -30% Servers

![Minus 30 Percent Latency](results/srv-30/latency.png)

![Minus 30 Percent Saturation](results/srv-30/saturation.png)

### Increased Server Capacity

#### +25% Servers

![Plus 25 Percent Latency](results/srv+25/latency.png)

![Plus 25 Percent Saturation](results/srv+25/saturation.png)

#### +50% Servers

![Plus 50 Percent Latency](results/srv+50/latency.png)

![Plus 50 Percent Saturation](results/srv+50/saturation.png)

#### +100% Servers

![Plus 100 Percent Latency](results/srv+100/latency.png)

![Plus 100 Percent Saturation](results/srv+100/saturation.png)

Additional utilization, error, throughput, longest-job, and accessory plots are available within their corresponding scenario directories.

## 10. Does the Behavior Make Sense for the Real-World Scenario?

Yes. The observed behavior matches what would be expected from a computer lab processing multiple student jobs.

When fewer processing slots are available, jobs are forced to wait longer before being processed. This increases latency and generally increases saturation.

When more processing slots are available, more jobs can be handled simultaneously, reducing queueing delays and improving average response time.

The fact that all jobs were completed is also reasonable for a system with sufficient queue capacity. However, the lack of dropped jobs does not necessarily mean that students would consider the system responsive.

The main limitation of the model is that every job performs the same fixed-duration task. In a real computer lab, job durations would vary depending on the software, hardware, and workload. The experiment also assumes the six queues operate independently.

Despite these simplifications, the model provides a useful demonstration of how changing resource capacity affects queueing performance.

## 11. Recommendations and Takeaways

1. **Monitor latency rather than only job completion.** Every scenario completed all 600 jobs, but average latency varied significantly.

2. **Provide enough processing capacity for periods of heavy demand.** Reducing server capacity by 30% increased average latency by approximately 46.5%.

3. **Consider additional processing resources when responsiveness is important.** Increasing capacity by 25% reduced average latency by approximately 20.4%.

4. **Monitor saturation alongside utilization.** High saturation indicates that jobs are accumulating while waiting for available processing resources.

5. **Avoid assuming that more resources always provide proportional improvements.** Doubling the servers reduced latency by approximately 50.5%, but the benefit would need to be weighed against the cost of additional resources.

For the computer lab, I would recommend maintaining enough processing slots to handle the normal workload while also allowing additional capacity during high-demand periods.

## 12. Conclusion

Overall, this experiment demonstrated how server capacity affects the performance of six independent queueing systems. The baseline configuration processed all 600 jobs with an average latency of 638.8 ms. Reducing the number of servers increased latency and generally increased saturation, while adding servers reduced both.

The most important takeaway is that a system can successfully complete every job without necessarily providing good response times. Queueing delays still matter, particularly when the available processing resources become fully utilized. For a university computer lab, the results suggest that sufficient processing capacity is important for maintaining responsiveness when multiple students submit jobs simultaneously. The sensitivity experiments and supporting plots demonstrate how utilization, saturation, errors, latency, and throughput can be used together to understand system behavior and evaluate changes in available resources.