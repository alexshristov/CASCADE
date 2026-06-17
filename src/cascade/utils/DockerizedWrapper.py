import os.path

import docker
import tarfile
import io

from docker.models.containers import Container
from cascade.utils.Metrics import time_block


class DockerizedWrapper:
    """
    This class is used to:
        create a docker container for the prepared tests directory,
        copy the directory into the docker,
        execute a (test) command and/or an evaluation command, and lastly
        kill and remove the container.
    """

    def __init__(self, debug=False):
        self.debug=debug
        pass


    def execute(self, dock_context: dict, output_path: str, copy: bool = False):
        """
        Executes a command in a docker container and evaluates the results (or copies a given file out of the container)

        :param dock_context: a dictionary containing the necessary information for the docker container. these are:
            image           - image is the docker image to run
            directory       - the prepared directory which contains everything to execute the test case, it will be copied into /root/
            command         - the command which executes the actual test case
            eval_command    - a command which returns information on the executed test cases
            eval_function   - a function which parses the output of the eval_command into succeeded, failed, errored
            path            - the path to the file to copy out of the container (only if copy = True)
        :param output_path: the path where the output (log file or copied contend) is stored. Also, any intermediate files are put there.
        :param copy: if True the file/folder specified in "path" in the dock_context dictionary is copied out of the container
        :return: the result of the eval_function on the output of the eval_command in the docker container
        """
        with time_block("docker_execute", image=dock_context.get("image"), copy=copy):
            result = None
            container = None
            try:
                container = self.set_up(dock_context)
                self.run(container, dock_context, output_path)

                if copy:
                    self.copy(container, dock_context, output_path)
                    result = True
                else:
                    result = self.eval(container, dock_context, output_path)

            finally:
                if container:
                    self.kill(container)

        return result


    def set_up(self, dock_context: dict):
        with time_block("docker_container_setup", image=dock_context.get("image")):
            client = docker.from_env(timeout=300)
            container = client.containers.run(dock_context["image"], "tail -f /dev/null", detach=True)
            if "directory" in dock_context:
                buffer = io.BytesIO()
                with tarfile.open(mode="w", fileobj=buffer) as tar:
                    tar.add(dock_context["directory"], arcname="")

                buffer.seek(0)
                container.put_archive("/root/", buffer)
            return container


    def run(self, container: Container, dock_context: dict, path):
        command_preview = dock_context["command"][:120]
        with time_block("docker_command", image=dock_context.get("image"), command_preview=command_preview):
            res = container.exec_run('bash -c - "cd ~; ' + dock_context["command"].replace('"', "\\\"") + '"')
        with open(os.path.join(path, "log.txt"), "a") as file:
            file.write("Command: " + dock_context["command"] + "\n")
            file.write(str(res.exit_code) + "\n")
            file.write(str(res.output, "utf-8") + "\n")
        if self.debug:
            print("Command:", dock_context["command"])
            print(res.exit_code)
            print(str(res.output, "utf-8"))
        return res.exit_code == 0


    def eval(self, container: Container, dock_context: dict, path):
        with time_block("docker_eval", image=dock_context.get("image"), eval_command=dock_context.get("eval_command")):
            res = container.exec_run('bash -c - "cd ~; ' + dock_context["eval_command"].replace('"', "\\\"") + '"')
        with open(os.path.join(path, "log.txt"), "a") as file:
            file.write("Eval Command: " + dock_context["eval_command"] + "\n")
            file.write(str(res.exit_code) + "\n")
            file.write(str(res.output, "utf-8") + "\n")
        if self.debug:
            print(res.exit_code)
            print(str(res.output, "utf-8"))
        return dock_context["eval_function"](str(res.output, "utf-8"))


    def kill(self, container: Container):
        with time_block("docker_cleanup"):
            container.kill()
            container.remove()


    def setup_image(self, dock_context: dict, output_path: str):
        with time_block("docker_image_setup", image=dock_context.get("image"), new_image=dock_context.get("new_image")):
            container = None
            client = docker.from_env(timeout=300)
            images = client.images.list(dock_context["new_image"])
            exit_code = False
            try:
                if images:
                    self.remove_image(dock_context)
                container = self.set_up(dock_context)
                exit_code = self.run(container, dock_context, output_path)
                container.commit(dock_context["new_image"])
            finally:
                if container:
                    self.kill(container)
            return exit_code


    def remove_image(self, dock_context: dict):
        with time_block("docker_image_remove", new_image=dock_context.get("new_image")):
            client = docker.from_env(timeout=300)
            try:
                image_name = dock_context["new_image"]
                containers = client.containers.list(all=True,
                                                    filters={"ancestor": image_name})  # Get all containers using the image
                for container in containers:
                    container.remove(force=True)  # Remove the containers forcefully

                client.images.remove(image_name, force=True)  # Now remove the image
            except Exception as e:
                print(f"Could not remove image because of Exception: {e}")

    def copy(self, container: Container, dock_context: dict, path):
        with time_block("docker_copy", copied_path=dock_context.get("path")):
            bits, stat = container.get_archive(dock_context['path'])
            tar_path = os.path.join(path, "temp_archive.tar")

            try:
                with open(tar_path, 'wb') as f:
                    for chunk in bits:
                        f.write(chunk)

                # Extract the tar file to the desired host path
                with tarfile.open(tar_path) as tar:
                    tar.extractall(path=path)

                os.remove(tar_path)

            except Exception as e:
                with open(os.path.join(path, "log.txt"), "a") as file:
                    file.write(f"Could not extract tar file because of Exception: {e}")

            with open(os.path.join(path, "log.txt"), "a") as file:
                file.write(f"copied file {dock_context['path']} to {path}\n")
                file.write(str(stat) + "\n")
