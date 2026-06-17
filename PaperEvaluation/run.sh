#!/bin/bash

SECONDS=0

unzip coreDataset.zip

WORKING_DIR=$(pwd)

#DRIVERS_LIST=()
#DRIVERS_LIST=("DocChecker" "Baseline" "C4RLLaMA")
DRIVERS_LIST=("CASCADE")

#  alter the drivers list to run the specific drivers you want, if the list is empty, it will run all the drivers in the drivers folder

(
cd java
for repo in */*
do
(
	echo "At repository: $repo ---------------------"
	cd "$repo"
	# clone this repo 
	git clone https://github.com/$repo ./repository
	
	for commit in *
	do
	
		if [ $commit == "repository" ]
		then continue
		fi

	(
		echo "    At commit: $commit -------"
		
		# checkout specific commit
		(cd repository; git checkout $commit)
		cd "$commit"
		cp -r "../repository" "./backup"
		
		#apply patch		
		(cd "./backup"; patch -p1 -f < ../file.patch)

		for num in *
		do
			if [[ $num == "backup" || $num == "file.patch" ]]
			then continue
			fi
		(	
			echo "        At number: $num"
			
			cd $WORKING_DIR/drivers
			if [ ${#DRIVERS_LIST[@]} -eq 0 ]; then
				drivers_to_run=(*)
		  	else
				drivers_to_run=("${DRIVERS_LIST[@]}")
			fi
			
			for driver in "${drivers_to_run[@]}";
			do
			(
				echo "copy $driver"
				
				# copy required stuff and execute
				cp -r $driver "../java/$repo/$commit/$num/$driver"
				cd "../java/$repo/$commit"
				cp -r "./backup" "./$num/$driver/repository"
				cd "./$num"
				cp "./analyzed.json" "./$driver/analyzed.json"
				cd "./$driver"
				
				bash driver.sh

				mv "result.txt" "../result_$driver.txt"
				mv "log.txt" "../log_$driver.txt"
				mv "errors.txt" "../errors_$driver.txt"	
				if [ -f "metrics.summary.json" ]; then
					mv "metrics.summary.json" "../metrics_summary_$driver.json"
				fi
				if [ -f "metrics.events.jsonl" ]; then
					mv "metrics.events.jsonl" "../metrics_events_$driver.jsonl"
				fi

				cp "analyzed.json" "../analyzed.json"
				#  -----------

				cd ..
				rm -rf $driver
			)
			done
		)		
		done
		# echo Press to continue
		# read  
		rm -rf ./backup		
	)				
	done
	rm -rf ./repository
)
done
(cd; cd "CASCADE"; git rev-parse HEAD >> $WORKING_DIR/runs; date >> $WORKING_DIR/runs;  echo >> $WORKING_DIR/runs)
)

printf 'Finished in %d h %02d m %02d s\n' $((SECONDS/3600)) $(((SECONDS/60)%60)) $((SECONDS%60))
