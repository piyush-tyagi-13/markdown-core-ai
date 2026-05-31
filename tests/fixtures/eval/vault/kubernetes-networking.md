# Kubernetes Pod Networking

Every pod in a Kubernetes cluster receives its own IP address. Pods on different
nodes communicate directly through the cluster network without NAT. The Container
Network Interface plugin wires up this flat network. A Service provides a stable
virtual IP that load balances traffic across the pods backing it, so clients never
talk to individual pod IPs that come and go. kube-proxy programs the routing rules
on each node to make Service IPs reachable from anywhere in the cluster.
