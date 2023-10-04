# -*- coding: utf-8 -*-

#
# opt_clustering.py
#
##############################################################################
#
# Copyright (c) 2019 Juan Carlos Saez<jcsaezal@ucm.es>, Jorge Casas <jorcasas@ucm.es>, Adrian Garcia Garcia<adriagar@ucm.es>
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
from simulator_core import *
import simulator_exploration as se
import datetime
import pytz

# Function that applies the UCP algorithm to an array of clusters
# Params: Array of clusters
# Return: UCP solution (lookahead algorithm)
def ucp_clustering_solution(clustering,nr_ways):
	## Weighted
	slowdown_curves=[]
	## Build per cluster slowdown curves
	for cluster in clustering:
		combined_slowdown=None
		for app in cluster:
			slowdown=app.get_metric_table("slowdown")
			if type(combined_slowdown) is pd.Series:
				combined_slowdown=combined_slowdown+slowdown
			else:
				combined_slowdown=slowdown
		slowdown_curves.append(combined_slowdown)

	return lookahead_algorithm_gen(slowdown_curves,nr_ways)

# Function that returns the app and cluster names
# Params: Array of apps and clusters
# Return: Tuple with apps and cluster names as strings
def plain_names(apps,cluster):
	n_apps=map(lambda x: x.name,apps)
	n_cluster=map(lambda c:(map(lambda x: x.name,c)),cluster)
	return n_apps,n_cluster

# Function that builds an array of clusters from an specific workload and an array of app assignments to clusters
# Params: Numeric cluster IDs and array of apps
# Return: Array of clusters
def get_application_clustering_from_app_ids(numeric_clustering,workload):
	clustering=[]
	for nclust in numeric_clustering:
		cluster=list(map(lambda id_app: workload[id_app],nclust))
		clustering.append(cluster)
	return clustering

# Function that calculates the best cost and best part solution for the clustering (no patched apps).
def determine_optimal_partitioning_for_clustering(num_clustering, workload, cost_function, max_bandwidth, best_cost, maximize, nr_ways , uoptions, multiprocessing=False,use_bf=True,**kwargs):
	## Transform low-level numeric representation into list of lists of object
	clustering=get_application_clustering_from_app_ids(num_clustering,workload)

	# Determine right operator
	op = operator.gt if maximize else operator.lt

	## Discard solutions that include more applications than ways
	## This should not occur
	assert(len(clustering)<=nr_ways)

	#################################################
	## CRITICAL FOR THE BW MODEL TO WORK CORRECTLY
	if kwargs["opt_mapping"]:
		## If mapping update LLC ID before patch
		for i,cluster in enumerate(clustering):
			## Set LLC ID for each app in subworkload
			for app in cluster:
				app.llc_id=i
	##################################################

	#print plain_names(patched_apps,patched_clustering)
	cp=kwargs["cache_part"]
	global_optimal=(kwargs["opt_mapping"]) and (type(cp) is str) and (cp.startswith("optc-"))
		
	## Optimization for trivial case
	if len(clustering)==1:
		local_solution=[nr_ways]
		(patched_clustering,patched_apps)=get_patched_clustering_and_workload(clustering,nr_ways)
		local_branches=1
		local_cost=cost_function((patched_apps,patched_clustering),local_solution, max_bandwidth)
		return (local_cost,local_solution,local_branches)
	elif kwargs["opt_mapping"]:
		if kwargs["cache_part"]=="lfoc+":
			local_branches=1
			lfoc_uoptions=kwargs.copy()
			lfoc_uoptions.update(uoptions)
			lfoc_uoptions["use_pair_clustering"]=True
			lfoc_uoptions["simple_output"]=True	
	
			clusters_part=[]
			way_distribution=[]
			## Get the original apps and apply LFOC for each LLC_ID
			## Each cluster is a separate LLC
			for cluster in clustering:
				subworkload=[app for app in cluster]
				## Invoke LFOC+ for that
				(subclusters,llc_way_distr)=se.lfoc(subworkload,nr_ways,max_bandwidth,lfoc_uoptions)	
				clusters_part.extend(subclusters)
				way_distribution.extend(llc_way_distr)

			(patched_clustering,patched_apps)=get_patched_clustering_and_workload(clusters_part,nr_ways)
			local_solution=way_distribution
		elif global_optimal:
			clusters_part=[]
			way_distribution=[]
			optc_uoptions=kwargs.copy()
			optc_uoptions.update(uoptions)
			local_branches=0

			for i,cluster in enumerate(clustering):
				subworkload=[]
				## disable for now
				for app in cluster:
					app.llc_id=-1
					subworkload.append(app)
				## Invoke Sequential optimal for that
				(subclusters,llc_way_distr,this_branch)=get_optimal_clustering_seq(subworkload,cost_function,maximize,nr_ways,max_bandwidth,False,simple_output=True,user_options=optc_uoptions)	
				local_branches=local_branches+this_branch
				## Reestablish llc_ids
				for scluster in  subclusters:
					for app in scluster:
						app.llc_id=i

				clusters_part.extend(subclusters)
				way_distribution.extend(llc_way_distr)				
			
			##Obtain patched global solution 
			(patched_clustering,patched_apps)=get_patched_clustering_and_workload(clusters_part,nr_ways)
			local_solution=way_distribution
							
		else:
			(patched_clustering,patched_apps)=get_patched_clustering_and_workload(clustering,nr_ways)
			local_branches=1
			## All ways in each llc
			local_solution=[nr_ways for i in range(len(patched_clustering))]

		local_cost=cost_function((patched_apps,patched_clustering),local_solution, max_bandwidth)		

			
		return (local_cost,local_solution,local_branches)
	else:
		(patched_clustering,patched_apps)=get_patched_clustering_and_workload(clustering,nr_ways)
		## Determine initial solution UCP-Slowdown for cluster
		ucp_solution=ucp_clustering_solution(patched_clustering,nr_ways)
		
		ucp_cost=cost_function((patched_apps,patched_clustering),ucp_solution, max_bandwidth)

		## Decide whether to pick this one or the previous one as the heuristic
		if (type(best_cost) is int and best_cost==0) or op(ucp_cost, best_cost):
			best_cost=ucp_cost
			best_solution=ucp_solution
		else:
			best_solution=None

		uopts=uoptions.copy()
		uopts["multiprocessing"]=False
		uopts["initial_bound"]=best_cost

		## Determine optimal partitioning for that clustering 
		if use_bf:
				(local_cost,local_solution,local_branches,metadata)=get_optimal_schedule_bf((patched_apps,patched_clustering),cost_function,maximize,nr_ways,max_bandwidth,user_options=uopts)
		else:
				(local_cost,local_solution,local_branches,metadata)=get_optimal_schedule((patched_apps,patched_clustering),cost_function,maximize,nr_ways,max_bandwidth,user_options=uopts)

		if local_solution is None:
			return (best_cost,best_solution,local_branches) ## UCP is the best
		else:
			return (local_cost,local_solution,local_branches)

		return (local_cost,local_solution,local_branches)

## Multiprocessor wrapper
def determine_optimal_partitioning_for_clustering_mp(num_clustering, best_cost):
	params=get_global_properties()
	## Force sequential execution
	params["multiprocessing"]=False
	params["best_cost"]=best_cost
	params["num_clustering"]=num_clustering

	return determine_optimal_partitioning_for_clustering(**params)

## Multiprocessor wrapper with arguments as tuple
def determine_optimal_partitioning_for_clustering_mp2(arg):
	(num_clustering, best_cost)=arg
	params=get_global_properties()
	## Force sequential execution
	params["multiprocessing"]=False
	params["best_cost"]=best_cost
	params["num_clustering"]=num_clustering

	return determine_optimal_partitioning_for_clustering(**params)


# Function that goes through an array of solutions and decides which one is best to be returned
def reduce_solutions(clusts, sols, op, best_solution , best_cost, total_branches):
	
	for i,sol in enumerate(sols):
		num_clustering=clusts[i]
		## Get optimal partitioning for that clustering solution
		(local_cost,local_solution,local_branches)=sol
		total_branches+=local_branches

		if local_solution is None:
			continue

		## Decide wether to pick this one or the previous one
		if  (best_solution is None) or  op(local_cost, best_cost):
			best_cost=local_cost
			best_solution=(num_clustering,local_solution) 
			#print "Best solution found Par:", best_solution, best_cost, total_branches

	return (best_solution,best_cost, total_branches)


# Function to process the exploration results and update the upper bound if a better solution is found
def clustering_process_result(task,clust,props,op):
	if task.cancelled():
		return False

	#node_queue
	#lb_queue
	ub=props["upper_bound"]
	total_nodes=props["total_nodes"]
	better_solution=True

	## Get result
	# If something errors out in one of the engines, the get() method is going to give poor error messages. 
	# All relevant information (errors, stdout) will be stored in task.metadata (see AsyncResult class)
	# Stack trace at: task.metadata['error'].print_traceback() or also task._exception.print_traceback()
	(this_cost,this_sol,node_count)=task.get() 

	## Get metadata
	if "trace" in props:
		add_trace_item(props["trace"],task.metadata,props["start_datet"],"1",include_master=True)

	## Update global node count 
	props["total_nodes"]=total_nodes+node_count

	## Found a better solution
	if (not this_sol is None) and ((props["incumbent"]  is None) or op(this_cost, ub)):
			## Update dict as well
			props["upper_bound"] = this_cost
			## Better solution includes the numeric clustering spec
			props["incumbent"] =  (clust, this_sol)
			#print("New solution found:", clust,this_sol,this_cost)
			better_solution=True	

	return better_solution

# Function to process pending tasks and mark them to be deleted if they have been completed or if they are no longer better that a new upper bound
def clust_process_pending(pending_tasks, num_clusts, clust_props,op, completed=None):
	## Traverse pending tasks
	i=0
	deleted=0
	better_solution_found=False

	if completed is None:
		## First stage (check pending tasks that completed)
		for task in pending_tasks[:]:
			if task.ready():
				if clustering_process_result(task,num_clusts[i],clust_props,op):
					better_solution_found=True
				pending_tasks.remove(task)	
				del num_clusts[i]	
				deleted=deleted+1
				#print "task", i, "completed. Removing from list"	
			else:
				i=i+1
	else:
		for task in completed:
			i=pending_tasks.index(task)
			if i==-1 or not task.ready():
				print("Error")
				exit(1)
			if clustering_process_result(task,clust_props,op):
				better_solution_found=True
			del pending_tasks[i]	
			del num_clusts[i]	
			deleted=deleted+1

	return deleted


# Public function that calculates best clustering solution for workload passed as a parameter
# Params: workload (array of Apps), cost function, maximize or not, slots available, max bandwidth, multiprocessing or sequential mode and activate pruning (bound) or not.
# Return: Best cost and best solution for the workload.
def get_optimal_clustering(workload, cost_function, maximize, nr_ways, max_bandwidth=float('Inf'), multiprocessing=False,**kwargs):

	## Set defaults
	kwargs.setdefault("user_options",None)
	kwargs.setdefault("chunk",1)
	kwargs.setdefault("async",False)
	kwargs.setdefault("sol_threshold", 20)
	kwargs.setdefault("paraver",False)
	kwargs.setdefault("max_cluster_size",len(workload))
	kwargs.setdefault("opt_mapping", False)
	kwargs.setdefault("cache_part",None)	
	uoptions=kwargs["user_options"]

	if uoptions:
		## Merge dict with user options... [Overriding defaults...]
		kwargs.update(uoptions)

	asyncr=kwargs["async"]
	chunk=kwargs["chunk"]
	sol_threshold=kwargs["sol_threshold"]
	paraver=kwargs["paraver"]
	max_cluster_size=kwargs["max_cluster_size"]

	metadatum = {}

	for i,app in enumerate(workload):
		app.bench_id = i

	if not asyncr:
		paraver=False

	if paraver:
		print("Activated paraver trace generation", file=sys.stderr)
		trace = []
		start = datetime.datetime.utcnow()
		unlocalized_start = pytz.utc.localize(datetime.datetime.utcnow())
		start_datet = unlocalized_start.astimezone(pytz.timezone("utc"))
		# States: 1 Blue(Parallel left over), 6 Light green (Parallel tasks), 7 Yellow (sequential)
		# 9 Dark green (Reductions), 13 Orange, 15 (Ugly green)

	# Determine right operator
	op = operator.gt if maximize else operator.lt

	iterator=generate_possible_clusters(range(len(workload)),nr_ways,max_cluster_size)

	best_solution=None
	best_cost=0
	total_branches=0

	## Determine table with number of solutions to drive execution
	nsols=number_of_solutions_partitioning_dp(nr_ways, min(len(workload),nr_ways))


	if multiprocessing:
		## Start connection right away
		rc = ipp.Client(timeout=30000) #debug=True)
		nr_engines=len(rc.ids)

		## Copy global data
		dview = rc[:]
		dict_globals={"workload": workload,
				  "cost_function": cost_function,
				  "max_bandwidth": max_bandwidth,
				  "nr_ways": nr_ways,
				  "maximize": maximize,
				  "uoptions": uoptions,
				  "multiprocessing": False,
				  "bw_model": kwargs["bw_model"],
				  "topology": kwargs["topology"],
				  "opt_mapping": False,
				  "cache_part": kwargs["cache_part"]}
		# Copy global data
		ret=dview.apply_sync(lambda x: set_global_properties(x),dict_globals)

		lview = rc.load_balanced_view()
		lview.block = not asyncr

	work_to_do=0
	tasks=[]

	for num_clustering in iterator:
		# Filter based on the number of solutions
		number_solutions=nsols[nr_ways-1][len(num_clustering)-1]


		if multiprocessing and number_solutions>=sol_threshold:
			## Do it later 
			tasks.append(num_clustering)
			work_to_do+=1

			if work_to_do>=nr_engines*chunk:

				if paraver:
					end = datetime.datetime.utcnow()
					(start_micros, end_micros) = get_start_end_micros(start, end, start_datet)
					trace.append(["1", "1", "1", "1", "1", str(start_micros), str(end_micros), "7"])

				# Send tasks to load balanced cluster
				sols= lview.map(lambda x: determine_optimal_partitioning_for_clustering_mp(x,best_cost),tasks)

				if asyncr:
					sols.get()
					if paraver:
						# Mark the time that too expanding the iterator
						metadata = sols.metadata

						for i, stats in enumerate(metadata):
							start_micros = int((stats["started"] - start_datet).total_seconds() * 1000000)
							end_micros = int((stats["completed"] - start_datet).total_seconds() * 1000000)
							trace.append(["1", "%i" % (stats['engine_id'] + 1), "1", "1", "%i" % (stats['engine_id'] + 1),
										  str(start_micros), str(end_micros), "6"])
				if paraver:
					start = datetime.datetime.utcnow()
				# Reduce sequentially 
				(best_solution,best_cost, total_branches)=reduce_solutions(tasks, sols, op, best_solution, best_cost, total_branches)
				## Clear list for next time
				work_to_do=0
				tasks=[]
		else:
			## Get optimal partitioning for that clustering solution
			(local_cost,local_solution,local_branches)=determine_optimal_partitioning_for_clustering(num_clustering, workload, cost_function, max_bandwidth, best_cost, maximize, nr_ways , uoptions, multiprocessing=False, **kwargs)
			total_branches+=local_branches

			if local_solution:
				## Decide wether to pick this one or the previous one
				if  best_solution is None or op(local_cost, best_cost):
					best_cost=local_cost
					best_solution=(num_clustering,local_solution) #(patched_apps,patched_clustering,local_solution)

	## Do remaining work (last iteration)
	if work_to_do>0:
		# Do it
		sols= lview.map(lambda x: determine_optimal_partitioning_for_clustering_mp(x,best_cost),tasks)

		if asyncr:
			sols.get()
			if paraver:
				metadata = sols.metadata

				for i, stats in enumerate(metadata):
					start_micros = int((stats["started"] - start_datet).total_seconds() * 1000000)
					end_micros = int((stats["completed"] - start_datet).total_seconds() * 1000000)
					trace.append(["1", "%i" % (stats['engine_id'] + 1), "1", "1", "%i" % (stats['engine_id'] + 1),
								  str(start_micros), str(end_micros), "1"])

		if paraver:
			start = datetime.datetime.utcnow()

		# Reduce sequentially 
		(best_solution,best_cost, total_branches)=reduce_solutions(tasks, sols, op, best_solution, best_cost, total_branches)

		if paraver:
			end = datetime.datetime.utcnow()
			(start_micros, end_micros) = get_start_end_micros(start, end, start_datet)
			trace.append(["1", "1", "1", "1", "1", str(start_micros), str(end_micros), "7"])

	if paraver:
		header_str = "#Paraver ({}/{}/{} at {}:{}):{}:1({}):1:1({}:1)\n".format(start_datet.day, start_datet.month,
																				start_datet.year, start_datet.hour,
																				start_datet.minute, end_micros,
																				nr_engines, nr_engines)
		metadatum["header"] = header_str
		metadatum["trace"] = trace

	##Prepare output... (Sorted maybe later)
	(num_clustering,clustering_sol)=best_solution
	#(patched_apps,patched_clustering,clustering_sol)=best_solution

	## Redo interpolation
	clustering=get_application_clustering_from_app_ids(num_clustering,workload)

	(patched_workload,per_app_ways,per_app_masks,cluster_ids)=normalize_output_for_clustering_solution(workload,clustering,clustering_sol,nr_ways)

	return (patched_workload,per_app_ways,per_app_masks,cluster_ids,total_branches,metadatum)

# Public function that calculates best clustering solution for workload passed as a parameter
# Params: workload (array of Apps), cost function, maximize or not, slots available, max bandwidth, multiprocessing or sequential mode and activate pruning (bound) or not.
# Return: Best cost and best solution for the workload.
def get_optimal_clustering_seq(workload, cost_function, maximize, nr_ways, max_bandwidth=float('Inf'), multiprocessing=False,**kwargs):

	## Set defaults
	kwargs.setdefault("user_options",None)
	kwargs.setdefault("simple_output",False)
	uoptions=kwargs["user_options"]

	if uoptions:
		## Merge dict with user options... [Overriding defaults...]
		kwargs.update(uoptions)

	if "max_cluster_size" in kwargs:
		max_cluster_size=kwargs["max_cluster_size"]
	else:
		max_cluster_size=len(workload)

# Determine right operator
	op = operator.gt if maximize else operator.lt
	
	iterator=generate_possible_clusters(workload,nr_ways,max_cluster_size)

	best_solution=None
	best_cost=0
	total_branches=0

	for clustering in iterator:
		patched_apps=[]
		patched_clustering=[]

		## Discard solutions that include more applications than ways
		## This should not occur
		assert(len(clustering)<=nr_ways)
			
		for cluster in clustering:
			patched_cluster=get_scaled_properties_cluster(cluster,nr_ways)
			patched_apps.extend(patched_cluster)
			patched_clustering.append(patched_cluster)
		
		#print plain_names(patched_apps,patched_clustering)

		## Optimization for trivial case
		if len(patched_clustering)==1:
			local_solution=[nr_ways]
			local_branches=1
			local_cost=cost_function((patched_apps,patched_clustering),local_solution, max_bandwidth)
		else:
			## Determine initial solution UCP-Slowdown for cluster
			ucp_solution=ucp_clustering_solution(patched_clustering,nr_ways)
			
			ucp_cost=cost_function((patched_apps,patched_clustering),ucp_solution, max_bandwidth)

			## Decide whether to pick this one or the previous one as the heuristic
			if (not best_solution) or op(ucp_cost, best_cost):
				best_cost=ucp_cost
				best_solution=(patched_apps,patched_clustering,ucp_solution)


			if multiprocessing and len(patched_clustering)>=3:
				go_parallel=True
			else:
				go_parallel=False

			uopts=uoptions.copy()
			uopts["multiprocessing"]=go_parallel
			uopts["initial_bound"]=best_cost

			## Determine optimal partitioning for that clustering 
			(local_cost,local_solution,local_branches,metadata)=get_optimal_schedule((patched_apps,patched_clustering),cost_function,maximize,nr_ways,max_bandwidth,user_options=uopts)
		
		total_branches+=local_branches

		## Update again 
		## Decide wether to pick this one or the previous one
		if  (not best_solution) or op(local_cost, best_cost):
			best_cost=local_cost
			best_solution=(patched_apps,patched_clustering,local_solution)


	## Prepare output
	(patched_apps,patched_clustering,clustering_sol)=best_solution

	## Plain case 
	if kwargs["simple_output"]:
		clean_clustering=[]
		for cluster in patched_clustering:
			clean_cluster=[]
			for app in cluster:
				clean_cluster.append(app.original_app)
			clean_clustering.append(clean_cluster)
		return (clean_clustering,clustering_sol,total_branches)

	## Build per cluster masks
	per_cluster_masks=get_partition_masks(clustering_sol)
	## Build per app ways and per app masks
	per_app_ways=[]
	per_app_masks=[]
	cluster_ids=[]
	for i,cluster in enumerate(patched_clustering):
		cluster_ways=clustering_sol[i]
		cluster_mask=per_cluster_masks[i]
		for app in cluster:
			per_app_ways.append(cluster_ways)
			per_app_masks.append(cluster_mask)
			cluster_ids.append(i)

	return (patched_apps,per_app_ways,per_app_masks,cluster_ids,total_branches)


# Public function that calculates best clustering solution for workload passed as a parameter
# Params: workload (array of Apps), cost function, maximize or not, slots available, max bandwidth, multiprocessing or sequential mode and activate pruning (bound) or not.
# Return: Best cost and best solution for the workload.
def get_optimal_clustering_bf(workload, cost_function, maximize, nr_ways, max_bandwidth=float('Inf'), multiprocessing=False,**kwargs):

	## Set defaults
	kwargs.setdefault("user_options",None)
	kwargs.setdefault("sol_threshold", 0)
	kwargs.setdefault("paraver",False)
	kwargs.setdefault("bw_model","simple")
	kwargs.setdefault("topology","uma")
	kwargs.setdefault("dyn_load_factor",2)
	kwargs.setdefault("paraver",False)
	kwargs.setdefault("debug",False)
	kwargs.setdefault("print_times",False)
	kwargs.setdefault("max_cluster_size",len(workload))
	kwargs.setdefault("cores_per_llc",4)
	kwargs.setdefault("max_core_groups",0) ## unlimited
	kwargs.setdefault("opt_mapping",False)
	kwargs.setdefault("cache_part",None)
	kwargs.setdefault("allow_unbalanced_mapping",True)

	uoptions=kwargs["user_options"]

	if uoptions:
		## Merge dict with user options... [Overriding defaults...]
		kwargs.update(uoptions)

	pq=[] ## Clustering queue
	num_clusts=[] ## Numeric clusters (for later recomposition)
	pending_tasks=[]
	
	sol_threshold=kwargs["sol_threshold"]
	paraver=kwargs["paraver"]
	dyn_load_factor= kwargs["dyn_load_factor"]
	print_times=kwargs["print_times"]
	debug=kwargs["debug"]
	log_enabled=paraver or print_times
	max_cluster_size=kwargs["max_cluster_size"]
	cores_per_llc=kwargs["cores_per_llc"]
	opt_mapping=kwargs["opt_mapping"]
	max_core_groups=kwargs["max_core_groups"]
	allow_unbalanced_mapping=kwargs["allow_unbalanced_mapping"]

	metadatum = {}

	reset_completion_variables()

	clust_props= { "node_queue": pq,
							"num_clusts": num_clusts,
						"upper_bound": 0, #previously initialized to None
						"incumbent": None,
						"total_nodes":0}

	if log_enabled:
		trace=[]
		start_datet=generate_start_datet()
		start=get_trace_timestamp()	
		clust_props["trace"]=trace
		clust_props["start_datet"]=start_datet	

	for i,app in enumerate(workload):
		app.bench_id = i


	# Determine right operator
	op = operator.gt if maximize else operator.lt

	if opt_mapping:
		nr_apps=len(workload)
		if max_core_groups==0:
			max_core_groups=(nr_apps+cores_per_llc-1)//cores_per_llc #GRAZ

		if allow_unbalanced_mapping:
			## Calculate max gaps at the end
			cores_unused=max_core_groups*cores_per_llc-nr_apps

			if cores_unused==0:
				min_apps_llc=min(cores_per_llc,nr_apps)
			elif cores_unused>=cores_per_llc:
				min_apps_llc=1
			else:
				min_apps_llc=cores_per_llc-cores_unused
		else:
			min_apps_llc=nr_apps//max_core_groups 
			if min_apps_llc==0:
				min_apps_llc=min(cores_per_llc,nr_apps)

			
		max_apps_llc=min(cores_per_llc,nr_apps)
		iterator=generate_possible_mappings(list(range(nr_apps)),min_apps_llc,max_apps_llc,max_core_groups)
	else:
		iterator=generate_possible_clusters(list(range(len(workload))),nr_ways,max_cluster_size)

		lfoc_uoptions=kwargs["user_options"].copy()
		lfoc_uoptions["use_pair_clustering"]=True
		lfoc_uoptions["benchmark_categories"]=kwargs["benchmark_categories"]
		
		## Use LFOC+ as an initial solution (heuristic)
		sol_data=se.lfoc(workload,nr_ways,max_bandwidth,uoptions=kwargs["user_options"])
		(sworkload,sper_app_ways,sper_app_masks,scluster_id)=sol_data
		## Count clusters
		ways_clust={}
		map_apps={}
		map_papps={}
		nr_clusters=0
		for i,clust_id in enumerate(scluster_id):
			if not (clust_id in ways_clust):
				ways_clust[clust_id]=sper_app_ways[i]
				nr_clusters=nr_clusters+1
				map_apps[clust_id]=[i]
				map_papps[clust_id]=[sworkload[i]]
			else:
				map_apps[clust_id].append(i)
				map_papps[clust_id].append(sworkload[i])
		
		num_clustering=[]
		ways_per_cluster=[]
		lf_clustering=[]
		for i in range(nr_clusters):
			num_clustering.append(map_apps[i])
			ways_per_cluster.append(ways_clust[i])
			lf_clustering.append(map_papps[i])

		## Falta determinar coste solucion optima
		lfcost=cost_function((sworkload,lf_clustering),ways_per_cluster,max_bandwidth)
		clust_props["upper_bound"] = lfcost
		clust_props["incumbent"] =(num_clustering,ways_per_cluster)

	
	## Determine table with number of solutions to drive execution
	nsols=number_of_solutions_partitioning_dp(nr_ways, min(len(workload),nr_ways))

	## Start connection right away
	rc = ipp.Client(timeout=30000)#, debug=True)
	nr_engines=len(rc.ids)

	## Copy global data
	dview = rc[:]
	dict_globals={"workload": workload,
			  "cost_function": cost_function,
			  "max_bandwidth": max_bandwidth,
			  "nr_ways": nr_ways,
			  "maximize": maximize,
			  "uoptions": uoptions,
			  "multiprocessing": False,
			  "use_bf": True, ## New parameter to force usage of best-first algorithm
			  "bw_model": kwargs["bw_model"],
			  "topology": kwargs["topology"],
			  "opt_mapping": opt_mapping,
			  "cache_part": kwargs["cache_part"]}
	# Copy global data
	ret=dview.apply_sync(lambda x: set_global_properties(x),dict_globals)

	lview = rc.load_balanced_view()
	lview.block = False

	## Populate 
	limit_queue=dyn_load_factor*nr_engines 

	for num_clustering in iterator:
		## While saturated queue
		while len(pq)>limit_queue:
			tasks_submitted=0

			## Send everything that we have until a certain limit
			while pq and len(pending_tasks)<=limit_queue:
				clust=pq.pop(0)
				task = lview.apply_async(determine_optimal_partitioning_for_clustering_mp2, (clust,clust_props["upper_bound"]))

				## Same callback as asynchronous stuff
				task.add_done_callback(bb_task_completed)	

				## Add pending task plus argument
				pending_tasks.append(task)
				num_clusts.append(clust)

				tasks_submitted=tasks_submitted+1	

			if tasks_submitted==0 and pending_tasks:
				if log_enabled:
					add_trace_seq_item(trace,start,get_trace_timestamp(),start_datet,"7")

				completed=wait_until_task_completed()
				if log_enabled:
					start=get_trace_timestamp()
				if len(completed)>0:
					clust_process_pending(pending_tasks, num_clusts, clust_props, op) # completed)

		## Enqueue new task if not a small B&B
		# Filter based on the number of solutions
		number_solutions=nsols[nr_ways-1][len(num_clustering)-1]

		# To force entering sequential mode
		# number_solutions=sol_threshold
		if number_solutions>sol_threshold:
			## Do it later 
			pq.append(num_clustering)
		else:
			## Get optimal partitioning for that clustering solution
			(local_cost,local_solution,local_branches)=determine_optimal_partitioning_for_clustering(num_clustering, workload, cost_function, max_bandwidth, 
				clust_props["upper_bound"], maximize, nr_ways , uoptions, multiprocessing=False, use_bf=True, opt_mapping=opt_mapping,
			  cache_part=kwargs["cache_part"])
			clust_props["total_nodes"]=clust_props["total_nodes"]+local_branches

			if not local_solution is None:
				## Update incumbent and cost
				clust_props["upper_bound"] = local_cost
				clust_props["incumbent"] =(num_clustering,local_solution)
				if debug:
					print("New solution found:", new_solution, this_cost)


	## Critical: send to the cluster everything else remaining to be processed
	tasks_submitted=0

	## Send everything that we have until a certain limit
	while pq:
		clust=pq.pop(0)
		task = lview.apply_async(determine_optimal_partitioning_for_clustering_mp2, (clust,clust_props["upper_bound"]))

		## Same callback as asynchronous stuff
		task.add_done_callback(bb_task_completed)	

		## Add pending task plus argument
		pending_tasks.append(task)
		num_clusts.append(clust)

		tasks_submitted=tasks_submitted+1	

	## Process pending tasks
	while pending_tasks:
		if log_enabled:
			add_trace_seq_item(trace,start,get_trace_timestamp(),start_datet,"7")

		completed=wait_until_task_completed()
		
		if log_enabled:
			start=get_trace_timestamp()
		if len(completed)>0:
			clust_process_pending(pending_tasks, num_clusts, clust_props, op) 	

	rc.close()

	(num_clustering,clustering_sol)=clust_props["incumbent"]
	
	## Redo interpolation
	clustering=get_application_clustering_from_app_ids(num_clustering,workload)

	#################################################
	## CRITICAL FOR THE BW MODEL TO WORK CORRECTLY
	if kwargs["opt_mapping"]:
		## If mapping update LLC ID before patch
		for i,cluster in enumerate(clustering):
			## Set LLC ID for each app in subworkload
			for app in cluster:
				app.llc_id=i
	##################################################

	## Rebuild solution for mapping/clustering case
	if opt_mapping and (type(kwargs["cache_part"]) is str):
		if kwargs["cache_part"]=="lfoc+" or kwargs["cache_part"]=="post-lfoc+":
			lfoc_uoptions=kwargs.copy()
			lfoc_uoptions.update(uoptions)
			lfoc_uoptions["use_pair_clustering"]=True
			lfoc_uoptions["simple_output"]=True	

			clusters_part=[]
			way_distribution=[]
			## Get the original apps and apply LFOC for each LLC_ID
			## Each cluster is a separate LLC
			for i,cluster in enumerate(clustering):
				subworkload=[app for app in cluster]
				## Invoke LFOC+ for that
				(subclusters,llc_way_distr)=se.lfoc(subworkload,nr_ways,max_bandwidth,lfoc_uoptions)	
				clusters_part.extend(subclusters)
				way_distribution.extend(llc_way_distr)


			(patched_workload,per_app_ways,per_app_masks,cluster_ids)=normalize_output_for_clustering_solution(workload,clusters_part,way_distribution,nr_ways)
		elif kwargs["cache_part"].startswith("optc-") or  kwargs["cache_part"].startswith("post-optc-"):
			clusters_part=[]
			way_distribution=[]
			
			for i,cluster in enumerate(clustering):
				subworkload=[]
				## disable for now
				for app in cluster:
					app.llc_id=-1
					subworkload.append(app)
				## Invoke Sequential optimal for that
				(subclusters,llc_way_distr,local_branches)=get_optimal_clustering_seq(subworkload,cost_function,maximize,nr_ways,max_bandwidth,False,simple_output=True,user_options=uoptions,max_cluster_size=max_cluster_size)	
				clust_props["total_nodes"]=clust_props["total_nodes"]+local_branches

				## Reestablish llc_ids
				for scluster in  subclusters:
					for app in scluster:
						app.llc_id=i

				clusters_part.extend(subclusters)
				way_distribution.extend(llc_way_distr)		
			(patched_workload,per_app_ways,per_app_masks,cluster_ids)=normalize_output_for_clustering_solution(workload,clusters_part,way_distribution,nr_ways)		
		else:
			(patched_workload,per_app_ways,per_app_masks,cluster_ids)=normalize_output_for_clustering_solution(workload,clustering,clustering_sol,nr_ways,opt_mapping)
	else:
		(patched_workload,per_app_ways,per_app_masks,cluster_ids)=normalize_output_for_clustering_solution(workload,clustering,clustering_sol,nr_ways,opt_mapping)

	if log_enabled:
		add_trace_seq_item(trace,start,get_trace_timestamp(),start_datet,"7")
		metadatum["header"]=generate_paraver_header(start_datet,nr_engines+1)
		metadatum["trace"]=trace

	return (patched_workload,per_app_ways,per_app_masks,cluster_ids,clust_props["total_nodes"],metadatum)

		