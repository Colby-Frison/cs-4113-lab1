## Logging Stack — Grafana Loki

### Research and Selection

For the logging portion of the project, I looked into a few different options for collecting and managing logs, mainly the ELK stack (Elasticsearch, Logstash, and Kibana) and Grafana Loki cause that was on the tasksheet and I choose laziness.

The ELK stack provides a pretty complete logging solution, with Elasticsearch handling storage and searching, Logstash processing logs, and Kibana providing a way to visualize everything. However, it can also be relatively resource-intensive, especially when running multiple services and indexing log contents.

Grafana Loki seemed like a better choice for our application because it is designed to be more lightweight and doesn't require full-text indexing of every log message. Instead, it primarily indexes labels, which helps reduce the resources required for log storage and indexing. This was especially important on a VM with limited memory, and the end goal of just wanting to demonstrate things on a lightweight scale.

I ultimately chose **Grafana Loki with Grafana Alloy and Grafana** because it provided the functionality we needed without the additional complexity of the ELK stack.

### Deployment

I deployed the logging stack into an existing Minikube Kubernetes cluster using Helm. The deployment consists of three main components:

- **Grafana Loki:** Stores and allows querying of collected logs.
- **Grafana Alloy:** Collects logs from the Kubernetes pods and forwards them to Loki.
- **Grafana:** Provides a web interface for searching, filtering, and visualizing the logs.

Loki was configured as a single-instance deployment with filesystem storage rather than a larger distributed deployment. This helped keep resource usage lower and was sufficient for the size of the application.

Alloy was configured to discover Kubernetes pods and collect their container logs. It also attaches metadata like the pod name, container name, and namespace, which makes it easier to filter logs from different parts of the application.

Grafana was configured to use Loki as its data source, allowing the collected logs to be viewed through the Explore interface.

### Testing and Verification

After deploying the stack, I verified that Loki, Alloy, and Grafana were running successfully in the Kubernetes cluster. I connected to Grafana and used its Explore interface to query the logs collected from the application's Kubernetes namespace. The following LogQL query can be used to filter logs from the smart-lock queue and function pods:

```logql
{namespace="default", pod=~"queue-q.*|function-q.*"}
```

Grafana successfully returned log data and displayed a log-volume graph, including 216 log entries during one query. This confirmed that the logging stack was collecting, storing, and retrieving Kubernetes logs which was the goal and I attached one of the screenshots I took in this directory.

### Conclusion

Overall, Loki was a good fit for this project because it was relatively simple to deploy, worked with the existing Kubernetes setup, and provided centralized logging without needing a larger logging infra. Using Grafana also made it easier to inspect the logs and troubleshoot the different components of the application.

### References

- [Grafana Loki Documentation](https://grafana.com/docs/loki/latest/)
- [Grafana Loki Installation](https://grafana.com/docs/loki/latest/setup/install/)
- [Grafana Alloy Documentation](https://grafana.com/docs/alloy/latest/)
- [Elastic Stack Documentation](https://www.elastic.co/docs/reference/)